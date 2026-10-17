"""AI Music Guard extension for web and authenticated mobile clients."""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import api, recognition_routes
from .ai_guard import AIGuard, normalized
from .library_catalog import list_library_entries

router = APIRouter(prefix='/api/v1/music')
_scan_tasks: set[int] = set()


def engine() -> AIGuard:
    if api.state.ai_guard is None:
        raise HTTPException(503, 'AI Music Guard is not ready')
    return api.state.ai_guard


class CheckInput(BaseModel):
    song: dict[str, Any] = Field(default_factory=dict)
    file: str = Field('', max_length=2000)
    recheck: bool = False


class FeedbackInput(BaseModel):
    assessment_id: str = Field('', max_length=64)
    target: str = Field('', max_length=300)
    action: Literal['allow', 'block', 'remove', 'allow_once']
    scope: Literal['track', 'artist', 'channel'] = 'track'
    note: str = Field('', max_length=1000)
    request_key: str = Field('', max_length=1000)
    global_rule: bool = False


class PolicyInput(BaseModel):
    enabled: bool = True
    mode: Literal['gentle', 'balanced', 'strict'] = 'balanced'
    hide_confirmed: bool = True
    hide_suspected: bool = False
    check_downloads: bool = True
    block_ai_covers: bool = True
    block_assisted: bool = False


class ScanInput(BaseModel):
    job_id: str = Field('', max_length=64)


@router.get('/ai-settings')
def settings(request: Request) -> dict[str, Any]:
    guard = engine()
    return {
        **guard.store.policy(),
        'overrides': guard.store.overrides(api.request_user_id(request)),
    }


@router.put('/ai-settings')
def set_settings(payload: PolicyInput, request: Request) -> dict[str, Any]:
    if not api.request_is_admin(request):
        raise HTTPException(403, 'This needs an admin')
    return engine().store.set_policy(payload.model_dump())


def file_song(file: str, request: Request) -> dict[str, Any]:
    path = recognition_routes.library_file(file, request)
    tags = api.read_audio_metadata(path)
    private_key = hashlib.sha256(
        f'{api.request_user_id(request)}:{file}:{path.stat().st_size}'.encode()
    ).hexdigest()
    return {
        'name': tags.get('title') or path.stem,
        'artist': tags.get('artist') or '',
        'song_id': private_key,
        'source': 'library',
    }


@router.post('/ai-check')
async def check(payload: CheckInput, request: Request) -> dict[str, Any]:
    guard = engine()
    song = (
        await asyncio.to_thread(file_song, payload.file, request)
        if payload.file
        else payload.song
    )
    if not song:
        raise HTTPException(400, 'A song or visible library file is required')
    # No caller-supplied assessment or metadata provenance is trusted.
    return await asyncio.to_thread(
        guard.assess,
        song,
        api.request_user_id(request),
        fetch=not bool(payload.file),
        force=payload.recheck,
    )


@router.get('/ai-status/{assessment_id}')
def status(assessment_id: str, request: Request) -> dict[str, Any]:
    guard, user_id = engine(), api.request_user_id(request)
    if not guard.store.visible(assessment_id, user_id):
        raise HTTPException(404, 'Assessment not found')
    result = guard.store.assessment(assessment_id, user_id, fresh=False)
    if result is None:
        raise HTTPException(404, 'Assessment not found')
    return guard.decision(result, user_id)


@router.post('/ai-feedback')
def feedback(payload: FeedbackInput, request: Request) -> dict[str, Any]:
    guard, user_id = engine(), api.request_user_id(request)
    if payload.global_rule:
        if not api.request_is_admin(request):
            raise HTTPException(403, 'This needs an admin')
        rule_user = 0
    else:
        rule_user = user_id
    if payload.action == 'remove' and payload.target:
        guard.store.override(
            rule_user, payload.scope, payload.target, 'remove', payload.note
        )
        return {'removed': True}
    data = status(payload.assessment_id, request)
    if payload.action == 'allow_once':
        return {
            'confirmation': guard.store.grant(
                user_id,
                payload.request_key or data['source_key'],
                data['source_key'],
            )
        }
    targets = {
        'track': data['id'],
        'artist': normalized(data['artist']),
        'channel': normalized(data.get('channel')),
    }
    target = targets[payload.scope]
    if not target:
        raise HTTPException(
            400, 'This assessment has no identity for that scope'
        )
    guard.store.override(
        rule_user, payload.scope, target, payload.action, payload.note
    )
    return status(data['id'], request)


async def run_scan(job: dict[str, Any], request: Request) -> None:
    user_id = api.request_user_id(request)
    guard = engine()
    try:
        for index in range(job['progress'], len(job['files'])):
            file = job['files'][index]
            try:
                song = await asyncio.to_thread(file_song, file, request)
                data = await asyncio.to_thread(guard.assess, song, user_id)
                job['totals'][data['status']] = (
                    job['totals'].get(data['status'], 0) + 1
                )
                # Keep detail links bounded, totals cover every file.
                if (
                    data['status'] in {'confirmed_ai', 'suspected_ai'}
                    and len(job['flagged']) < 500
                ):
                    job['flagged'].append({'file': file, 'assessment': data})
            except Exception:
                job['errors'] += 1
            job['progress'] = index + 1
            if job['progress'] % 10 == 0 or job['progress'] == len(
                job['files']
            ):
                await asyncio.to_thread(guard.store.save_scan, job, user_id)
        job.update(status='done', completed_at=time.time())
    except asyncio.CancelledError:
        job['status'] = 'interrupted'
        raise
    finally:
        guard.store.save_scan(job, user_id)
        _scan_tasks.discard(user_id)


@router.post('/library/ai-scan', status_code=202)
async def scan(payload: ScanInput, request: Request) -> dict[str, Any]:
    guard, user_id = engine(), api.request_user_id(request)
    if user_id in _scan_tasks:
        raise HTTPException(
            409, 'A library scan is already running for this account'
        )
    _scan_tasks.add(user_id)
    try:
        if payload.job_id:
            job = guard.store.scan(payload.job_id, user_id)
            if job is None:
                raise HTTPException(404, 'Scan not found')
            job['status'] = 'running'
        else:
            entries = await asyncio.to_thread(
                list_library_entries,
                api.library_context(),
                allowed_keys=api.allowed_entry_keys(request),
            )
            job = {
                'id': uuid.uuid4().hex,
                'status': 'running',
                'files': list(
                    dict.fromkeys(
                        str(e['file']) for e in entries if e.get('file')
                    )
                ),
                'progress': 0,
                'totals': {},
                'errors': 0,
                'flagged': [],
                'started_at': time.time(),
            }
        job['total'] = len(job['files'])
        guard.store.save_scan(job, user_id)
        api.spawn_task(run_scan(job, request), name=f'ai-scan-{job["id"]}')
    except BaseException:
        _scan_tasks.discard(user_id)
        raise
    return {key: value for key, value in job.items() if key != 'files'}


@router.get('/library/ai-scan/{job_id}')
def scan_status(job_id: str, request: Request) -> dict[str, Any]:
    job = engine().store.scan(job_id, api.request_user_id(request))
    if job is None:
        raise HTTPException(404, 'Scan not found')
    return {key: value for key, value in job.items() if key != 'files'}

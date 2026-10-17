"""Authenticated AMV recognition API, shared by the web UI and clients."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import api, recognition, recognition_tags
from .library_catalog import list_library_entries
from .recognition_store import ACTIVE, RecognitionStore

router = APIRouter(prefix='/api/music')


class RecognizeInput(BaseModel):
    url: str = Field('', max_length=2000)
    file: str = Field('', max_length=2000)
    use_audio: bool = False
    retry: bool = False


class DecisionInput(BaseModel):
    action: Literal['accept', 'reject']
    title: str = Field('', max_length=200)
    artist: str = Field('', max_length=200)
    candidate_index: int | None = Field(None, ge=0, lt=20)


class ScanInput(BaseModel):
    use_audio: bool = False
    retry_failed: bool = False


class TagsInput(BaseModel):
    action: Literal['apply', 'undo']


def store() -> RecognitionStore:
    if api.state.recognition is None:
        raise HTTPException(503, 'Recognition store is not ready')
    return api.state.recognition


def library_file(file: str, request: Request) -> Path:
    """Resolve through the existing library boundary and ownership rules."""
    full = api.resolve_library_file(file, api.library_context())
    if full is None:
        raise HTTPException(404, 'Library file not found')
    allowed = api.allowed_entry_keys(request)
    if allowed is not None and api.stored_content_key(file) not in allowed:
        raise HTTPException(404, 'Library file not found')
    return full


def source_for(payload: RecognizeInput, request: Request) -> dict[str, Any]:
    if bool(payload.file.strip()) == bool(payload.url.strip()):
        raise HTTPException(
            400, 'Provide exactly one library file or YouTube link'
        )
    if payload.file:
        file = payload.file.strip().replace('\\', '/')
        full = library_file(file, request)
        tags = api.read_audio_metadata(full)
        stat = full.stat()
        return {
            'key': f'file:{file}',
            'file': file,
            'url': '',
            'title': tags.get('title') or full.stem,
            'channel': '',
            'tag_artist': tags.get('artist') or '',
            'duration': tags.get('duration') or 0,
            'size': stat.st_size,
            'mtime_ns': stat.st_mtime_ns,
        }
    try:
        return recognition.youtube_source(payload.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def checked_job(job_id: str, request: Request) -> dict[str, Any]:
    job = store().get(job_id, api.request_user_id(request))
    if job is None:
        raise HTTPException(404, 'Recognition not found')
    if job['source'].get('file'):
        library_file(job['source']['file'], request)
    return job


async def run_job(
    job: dict[str, Any], user_id: int, cookies: str, path: Path | None
) -> None:
    db = store()
    if api.state.recognition_semaphore is None:
        api.state.recognition_semaphore = asyncio.Semaphore(2)
    try:
        async with api.state.recognition_semaphore:
            await asyncio.to_thread(db.update, job['id'], user_id, 'running')
            result = await asyncio.to_thread(
                recognition.recognize,
                job['source'],
                path=path,
                cookies_file=cookies,
                use_audio=job['source'].get('use_audio', False),
            )
            status = 'review' if result['candidates'] else 'no_candidates'
            await asyncio.to_thread(
                db.update, job['id'], user_id, status, **result
            )
    except asyncio.CancelledError:
        db.update(job['id'], user_id, 'interrupted')
        raise
    except Exception:
        # Do not expose provider exceptions: they may contain tokens or
        # signed stream URLs. Source and manual correction remain usable.
        await asyncio.to_thread(
            db.update,
            job['id'],
            user_id,
            'error',
            warnings=['Recognition failed; retry or enter the song manually'],
        )


@router.get('/recognition/capabilities')
def recognition_capabilities() -> dict[str, Any]:
    return {**recognition.capabilities(), 'metadata': True}


@router.get('/recognition')
def recent_recognition(request: Request) -> list[dict[str, Any]]:
    jobs = store().recent(api.request_user_id(request))
    visible = []
    for job in jobs:
        try:
            if job['source'].get('file'):
                library_file(job['source']['file'], request)
        except HTTPException:
            continue
        visible.append(job)
    return visible


@router.post('/recognize', status_code=202)
async def start_recognition(
    payload: RecognizeInput, request: Request
) -> dict[str, Any]:
    source = await asyncio.to_thread(source_for, payload, request)
    source['use_audio'] = payload.use_audio
    user_id = api.request_user_id(request)
    try:
        job, created = await asyncio.to_thread(
            store().create, source, user_id, retry=payload.retry
        )
    except ValueError as exc:
        raise HTTPException(429, str(exc)) from exc
    if created:
        path = (
            library_file(source['file'], request)
            if source.get('file')
            else None
        )
        cookies = api.stream.resolve_cookies_file(api.state.cookies_store)
        api.spawn_task(
            run_job(job, user_id, cookies, path),
            name=f'recognition-{job["id"]}',
        )
    return job


@router.get('/recognition/{job_id}')
def get_recognition(job_id: str, request: Request) -> dict[str, Any]:
    return checked_job(job_id, request)


@router.post('/library/scan', status_code=202)
async def scan_library(payload: ScanInput, request: Request) -> dict[str, Any]:
    """Queue the next ten unseen files. Persisted jobs are the checkpoint."""
    entries = await asyncio.to_thread(
        list_library_entries,
        api.library_context(),
        allowed_keys=api.allowed_entry_keys(request),
    )
    jobs: list[dict[str, Any]] = []
    remaining = 0
    for entry in entries:
        file = str(entry.get('file') or '')
        if not file:
            continue
        previous = await asyncio.to_thread(
            store().latest, f'file:{file}', api.request_user_id(request)
        )
        if previous and not (
            payload.retry_failed
            and previous['status'] in {'interrupted', 'error'}
        ):
            continue
        if len(jobs) >= 10:
            remaining += 1
            continue
        try:
            job = await start_recognition(
                RecognizeInput(
                    file=file,
                    use_audio=payload.use_audio,
                    retry=bool(previous),
                ),
                request,
            )
        except HTTPException as exc:
            if exc.status_code == 429:
                remaining += 1
                continue
            raise
        jobs.append(job)
    return {'jobs': jobs, 'remaining': remaining}


@router.patch('/recognition/{job_id}')
def decide_recognition(
    job_id: str, payload: DecisionInput, request: Request
) -> dict[str, Any]:
    job = checked_job(job_id, request)
    if job.get('tag_backup'):
        raise HTTPException(
            409, 'Undo the tag correction before changing this identity'
        )
    if job['status'] in ACTIVE:
        raise HTTPException(409, 'Wait for recognition to finish')
    if payload.action == 'reject':
        return store().update(
            job_id, api.request_user_id(request), 'rejected', selected=None
        )
    selected = {}
    if payload.candidate_index is not None:
        if payload.candidate_index >= len(job['candidates']):
            raise HTTPException(400, 'Candidate not found')
        selected = dict(job['candidates'][payload.candidate_index])
    title = payload.title.strip() or str(selected.get('title') or '')
    artist = payload.artist.strip() or str(selected.get('artist') or '')
    if not title or not artist:
        raise HTTPException(400, 'Both song title and artist are required')
    # Editing an audio match must not retain a different song's video id.
    if title != selected.get('title') or artist != selected.get('artist'):
        selected = {'method': 'manual'}
    selected.update(title=title, artist=artist)
    if job['source'].get('file'):
        full = library_file(job['source']['file'], request)
        stat = full.stat()
        if (stat.st_size, stat.st_mtime_ns) != (
            job['source']['size'],
            job['source']['mtime_ns'],
        ):
            raise HTTPException(
                409, 'The library file changed; run recognition again'
            )
    return store().update(
        job_id, api.request_user_id(request), 'accepted', selected=selected
    )


def approved_seed(
    file: str, url: str, request: Request
) -> dict[str, Any] | None:
    source = source_for(RecognizeInput(file=file, url=url), request)
    job = store().approved(source['key'], api.request_user_id(request))
    if job and file:
        previous = job['source']
        if (source['size'], source['mtime_ns']) != (
            previous['size'],
            previous['mtime_ns'],
        ):
            return None
    return job


@router.get('/identity')
def get_identity(
    request: Request,
    file: str = Query('', max_length=2000),
    url: str = Query('', max_length=2000),
) -> dict[str, Any]:
    return {'recognition': approved_seed(file, url, request)}


def _edit_tags(
    job: dict[str, Any], request: Request, action: str
) -> dict[str, Any]:
    file = str(job['source'].get('file') or '')
    full = library_file(file, request)
    if job['status'] != 'accepted':
        raise HTTPException(409, 'Approve the song identity first')
    if not api.state.settings_path:
        raise HTTPException(503, 'Server data directory is not ready')
    # Only a server-created id can reach here (checked_job read it from DB).
    backup = api.state.settings_path.parent / 'recognition-backups' / job['id']
    old_key = api.stored_content_key(file)
    try:
        if action == 'apply':
            stat = full.stat()
            if (stat.st_size, stat.st_mtime_ns) != (
                job['source']['size'],
                job['source']['mtime_ns'],
            ):
                raise ValueError('The file changed; run recognition again')
            expected = recognition_tags.apply_tags(
                full,
                backup,
                job['selected']['title'],
                job['selected']['artist'],
            )
        else:
            if not job.get('tag_backup'):
                raise ValueError('There is no tag correction to undo')
            recognition_tags.restore_tags(
                full, backup, job.get('tag_digest', '')
            )
            expected = ''
        stat = full.stat()
        source = {
            **job['source'],
            'size': stat.st_size,
            'mtime_ns': stat.st_mtime_ns,
        }
        try:
            result = store().update(
                job['id'],
                api.request_user_id(request),
                'accepted',
                source=source,
                tag_backup=action == 'apply',
                tag_digest=expected,
            )
        except Exception:
            if action == 'apply':
                recognition_tags.restore_tags(full, backup, expected)
                backup.unlink(missing_ok=True)
            raise
        if action == 'undo':
            backup.unlink(missing_ok=True)
        api._after_replacement(file, full, old_key)
        api.announce_library_changed()
        return result
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/recognition/{job_id}/tags')
async def edit_recognized_tags(
    job_id: str, payload: TagsInput, request: Request
) -> dict[str, Any]:
    if not api.request_is_admin(request):
        raise HTTPException(403, 'This needs an admin')
    job = checked_job(job_id, request)
    file = str(job['source'].get('file') or '')
    if not file:
        raise HTTPException(
            400, 'Tag correction requires a local library file'
        )
    if file in api._replacing:
        raise HTTPException(409, 'This file is already being edited')
    api._replacing.add(file)
    try:
        return await asyncio.to_thread(
            _edit_tags, job, request, payload.action
        )
    finally:
        api._replacing.discard(file)


@router.get('/similar')
async def similar_for_source(
    request: Request,
    file: str = Query('', max_length=2000),
    url: str = Query('', max_length=2000),
    artist: str = Query('', max_length=200),
    track: str = Query('', max_length=200),
    limit: int = Query(20, ge=1, le=50),
) -> dict[str, Any]:
    job = await asyncio.to_thread(approved_seed, file, url, request)
    if job:
        artist, track = job['selected']['artist'], job['selected']['title']
    try:
        result = await asyncio.to_thread(
            api.providers.youtube_similar_tracks, artist, track, limit=limit
        )
    except ValueError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {**result, 'recognition': job}

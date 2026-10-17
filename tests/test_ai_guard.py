"""Offline origin classification, source-version checks and queue enforcement."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

import main
from downtify import ai_guard, api
from downtify import ai_guard_routes as routes
from downtify.ai_guard import AIGuard, MusicGuardBlocked, classify, source_key
from downtify.ai_guard_middleware import AIGuardMiddleware, filter_result
from downtify.ai_guard_store import DEFAULT_POLICY, AIGuardStore
from downtify.downloader import Downloader

VIDEO = 'abcdefghijk'
COVER = 'lmnopqrstuv'
SONG = {
    'song_id': VIDEO,
    'name': 'Broken',
    'artists': ['Actual Musician'],
    'source': 'youtube',
    'url': f'https://www.youtube.com/watch?v={VIDEO}',
}


def request(user_id=1, admin=False):
    return Request({
        'type': 'http',
        'headers': [],
        'state': {
            'principal': SimpleNamespace(user_id=user_id, is_admin=admin)
        },
    })


@pytest.fixture
def guard(tmp_path, monkeypatch):
    engine = AIGuard(AIGuardStore(tmp_path / 'guard.db'))
    monkeypatch.setattr(api.state, 'ai_guard', engine)
    monkeypatch.setattr(api.state, 'download_jobs', {})
    monkeypatch.setattr(api.state, 'background_tasks', set())
    monkeypatch.setattr(
        ai_guard.stream,
        'video_info_for_recognition',
        lambda *a, **kw: {
            'title': 'Broken',
            'artist': 'Actual Musician',
            'description': 'Recorded with our band',
            'channel_id': 'UCtest',
        },
    )
    return engine


def test_keyword_is_suspicion_not_proof():
    data = classify({'name': 'Suno AI cover of Broken'}, f'youtube:{VIDEO}')
    assert data['status'] == 'suspected_ai'
    assert data['kind'] == 'ai_cover'
    assert data['evidence'][0]['strength'] == 'weak'


def test_only_server_source_declaration_can_confirm_ai():
    description = 'This song was generated using Suno'
    assert (
        classify({'description': description}, 'catalog:1')['status']
        == 'suspected_ai'
    )
    data = classify({}, f'youtube:{VIDEO}', {'description': description})
    assert data['status'] == 'confirmed_ai'
    assert data['evidence'][0]['source_url'].endswith(VIDEO)
    assert data['rule_version']
    assert data['expires_at'] > data['checked_at']


@pytest.mark.parametrize(
    'title',
    ['Not AI generated music', 'No Suno or Udio was used', 'Against AI music'],
)
def test_negative_declarations_do_not_trigger(title):
    assert classify({'name': title}, 'catalog:1')['status'] == 'unknown'


def test_absence_of_evidence_is_not_human_authorship():
    assert classify(SONG, f'youtube:{VIDEO}')['status'] == 'unknown'
    data = classify(
        SONG, f'youtube:{VIDEO}', {'description': 'We recorded this on tour'}
    )
    assert data['status'] == 'no_ai_evidence'
    assert 'not proof' in data['explanation']


def test_mixed_generated_and_mastering_cannot_downgrade_block(guard):
    data = classify(
        {},
        'catalog:mixed',
        {
            'description': 'This song was generated using Suno. AI mastering was used too.'
        },
    )
    assert guard.decision(data, 1)['action'] == 'block'


def test_policy_modes_and_assisted_preference(guard):
    suspect = {'name': 'AI cover', 'artist': 'Test'}
    assert guard.assess(suspect, 1)['action'] == 'confirm'
    guard.store.set_policy({**DEFAULT_POLICY, 'mode': 'gentle'})
    assert guard.assess(suspect, 1)['action'] == 'allow'
    guard.store.set_policy({**DEFAULT_POLICY, 'mode': 'strict'})
    assert guard.assess(SONG, 1)['action'] == 'confirm'
    assert (
        guard.assess({'name': 'AI mastered', 'song_id': 'assisted'}, 1)[
            'action'
        ]
        == 'allow'
    )
    guard.store.set_policy({**DEFAULT_POLICY, 'block_assisted': True})
    assert (
        guard.assess({'name': 'AI mastered', 'song_id': 'assisted'}, 1)[
            'action'
        ]
        == 'confirm'
    )


def test_manual_decisions_are_scoped_to_account_and_exact_recording(guard):
    data = guard.assess(SONG, 1)
    guard.store.override(1, 'track', data['id'], 'block', 'My report')
    assert guard.assess(SONG, 1)['action'] == 'block'
    assert guard.assess(SONG, 2)['action'] == 'allow'
    guard.store.override(1, 'track', data['id'], 'allow', 'Checked it myself')
    assert guard.assess(SONG, 1)['status'] == 'user_allowed'
    assert source_key(SONG) != source_key(SONG, COVER)


def test_allowing_original_artist_does_not_allow_ai_cover(guard):
    guard.store.override(
        1, 'artist', 'actual musician', 'allow', 'Trust original artist'
    )
    data = classify(
        {'name': 'AI cover', 'artist': 'Actual Musician'}, f'youtube:{COVER}'
    )
    assert guard.decision(data, 1)['action'] == 'confirm'


def test_actual_download_version_is_checked_before_network_fetch(
    guard, monkeypatch, tmp_path
):
    monkeypatch.setattr(
        ai_guard.stream,
        'video_info_for_recognition',
        lambda *a, **kw: {
            'title': 'Broken AI Cover',
            'artist': 'Someone Else',
            'description': 'This song was generated using Suno',
        },
    )
    downloader = Downloader(tmp_path)
    downloader.music_guard = guard
    with guard.context(1, SONG), pytest.raises(MusicGuardBlocked) as error:
        downloader.fetch_audio(COVER, tmp_path, 'must-not-exist', song=SONG)
    assert error.value.assessment['source_video_id'] == COVER
    assert not list(tmp_path.glob('must-not-exist*'))
    with pytest.raises(MusicGuardBlocked):
        guard.preflight(SONG, 2)


def test_once_grants_are_single_use_user_and_version_bound(guard):
    suspect = {**SONG, 'name': 'AI cover'}
    data = guard.assess(suspect, 1)
    token = guard.store.grant(1, source_key(suspect), data['source_key'])
    assert not guard.store.check_grant(token, 2, source_key(suspect))
    assert not guard.store.check_grant(
        token, 1, source_key(suspect), f'youtube:{COVER}'
    )
    guard.store.check_grant(
        token, 1, source_key(suspect), data['source_key'], consume=True
    )
    assert not guard.store.check_grant(token, 1, source_key(suspect))


def test_opaque_client_bypass_flags_do_nothing(guard):
    song = {
        **SONG,
        'name': 'AI cover',
        'ai_guard': {'status': 'user_allowed'},
        'ai_allowed': True,
        'ai_confirmation': 'forged',
    }
    with pytest.raises(MusicGuardBlocked):
        guard.preflight(song, 1)


def test_queue_blocks_before_registering_job(guard):
    with pytest.raises(HTTPException) as error:
        api._register_job({**SONG, 'name': 'AI cover'}, user_id=1)
    assert error.value.status_code == 409
    assert error.value.detail['code'] == 'ai_guard'
    assert api.state.download_jobs == {}


def test_batch_reports_all_skips_without_creating_jobs(guard):
    songs = [{**SONG, 'name': 'AI cover'}]
    result = asyncio.run(
        api._submit_playlist_batch(songs, '', generate_m3u=False, user_id=1)
    )
    assert result['count'] == 0
    assert (
        result['ai_guard_skipped'][0]['assessment']['status'] == 'suspected_ai'
    )
    assert api.state.download_jobs == {}


def test_confirmed_recording_is_hidden_from_all_recommendation_shapes(
    guard, monkeypatch
):
    data = classify(
        SONG,
        source_key(SONG),
        {'description': 'This song was generated using Suno'},
    )
    data['metadata_attempted'] = True
    guard.store.save(data, 1)
    result = filter_result({'tracks': [SONG], 'albums': [], 'artists': []}, 1)
    assert result['tracks'] == []
    assert result['ai_guard_summary']['hidden'] == 1
    assert filter_result([SONG], 1) == []


def test_verified_cache_is_not_downgraded_by_provider_outage(
    guard, monkeypatch
):
    data = classify(
        SONG,
        source_key(SONG),
        {'description': 'This song was generated using Suno'},
    )
    guard.store.save(data, 1)
    monkeypatch.setattr(
        ai_guard.stream,
        'video_info_for_recognition',
        lambda *a, **kw: (_ for _ in ()).throw(ValueError('offline')),
    )
    assert (
        guard.assess(SONG, 1, fetch=True, force=True)['status']
        == 'confirmed_ai'
    )


def test_middleware_filters_actual_http_response_and_keeps_headers(guard):
    data = classify(
        SONG,
        source_key(SONG),
        {'description': 'This song was generated using Suno'},
    )
    guard.store.save(data, 0)
    app = FastAPI()
    app.add_middleware(AIGuardMiddleware)

    @app.get('/api/similar/tracks')
    def similar():
        return {'tracks': [SONG]}

    with TestClient(app) as client:
        response = client.get('/api/similar/tracks')
    assert response.status_code == 200
    assert response.json()['tracks'] == []
    assert int(response.headers['content-length']) == len(response.content)


def test_settings_and_feedback_enforce_ownership(guard):
    data = guard.assess(SONG, 1)
    with pytest.raises(HTTPException) as error:
        routes.status(data['id'], request(2))
    assert error.value.status_code == 404
    with pytest.raises(HTTPException):
        routes.set_settings(routes.PolicyInput(mode='strict'), request(1))
    assert (
        routes.set_settings(
            routes.PolicyInput(mode='strict'), request(1, True)
        )['mode']
        == 'strict'
    )
    result = routes.feedback(
        routes.FeedbackInput(assessment_id=data['id'], action='allow'),
        request(1),
    )
    assert result['status'] == 'user_allowed'
    assert guard.assess(SONG, 2)['status'] == 'unknown'


def test_shared_job_does_not_give_blocked_version_to_another_account(guard):
    data = classify(
        SONG,
        source_key(SONG),
        {'description': 'This song was generated using Suno'},
    )
    guard.store.save(data, 1)
    guard.store.override(1, 'track', data['id'], 'allow', 'Explicit decision')
    assert guard.shareable(SONG, {1, 2}) == {1}


def test_local_metadata_cache_cannot_be_retrieved_by_another_user(guard):
    song = {
        'source': 'library',
        'song_id': 'private-file',
        'name': 'Private rehearsal',
    }
    data = guard.assess(song, 1)
    other = guard.assess({**song, 'name': 'Unknown'}, 2)
    assert data['id'] != other['id']
    assert other['title'] == 'Unknown'
    forged = guard.assess(
        {
            'source': 'private-library:1:library',
            'song_id': 'private-file',
            'name': 'Forged',
        },
        2,
    )
    assert forged['id'] != data['id']


def test_local_scan_only_reads_owned_metadata_and_never_deletes(
    guard, monkeypatch, tmp_path
):
    path = tmp_path / 'AI cover.mp3'
    path.write_bytes(b'unchanged-audio')
    monkeypatch.setattr(
        routes.recognition_routes, 'library_file', lambda file, req: path
    )
    monkeypatch.setattr(
        api,
        'read_audio_metadata',
        lambda file: {'title': 'AI cover', 'artist': 'Test'},
    )
    job = {
        'id': 'scan',
        'status': 'running',
        'files': [path.name],
        'progress': 0,
        'totals': {},
        'flagged': [],
        'errors': 0,
    }
    asyncio.run(routes.run_scan(job, request(1)))
    assert job['progress'] == 1
    assert job['totals'] == {'suspected_ai': 1}
    assert path.read_bytes() == b'unchanged-audio'
    assert guard.store.scan('scan', 2) is None
    asyncio.run(routes.run_scan(job, request(1)))
    assert job['totals'] == {'suspected_ai': 1}


def test_album_skips_blocked_tracks_and_keeps_legacy_response_shape(
    guard, monkeypatch
):
    monkeypatch.setattr(api.state, 'downloader', SimpleNamespace())
    monkeypatch.setattr(
        api,
        '_songs_for_album_download',
        lambda url: [{**SONG, 'name': 'AI cover'}],
    )
    monkeypatch.setattr(api, 'log_activity', lambda *a, **kw: asyncio.sleep(0))
    report = asyncio.run(
        api.download_album_endpoint(request(1), url='album', guard_report=True)
    )
    assert report['downloads'] == {}
    assert len(report['ai_guard_skipped']) == 1
    assert (
        asyncio.run(api.download_album_endpoint(request(1), url='album')) == {}
    )


def test_guard_is_wired_after_library_startup(guard, monkeypatch, tmp_path):
    downloader = Downloader(tmp_path / 'downloads')
    monkeypatch.setattr(main, 'DATABASE_DIR', tmp_path)
    monkeypatch.setattr(api.state, 'downloader', downloader)
    main._open_library_stores(tmp_path / 'monitor.db')
    assert downloader.music_guard is api.state.ai_guard
    assert downloader.music_guard is not None


def test_original_artist_exception_cannot_authorize_uncredited_generated_version(
    guard,
):
    guard.store.override(
        1, 'artist', 'actual musician', 'allow', 'Original artist only'
    )
    data = classify(
        SONG,
        f'youtube:{COVER}',
        {
            'title': 'Broken',
            'description': 'This song was generated using Suno',
        },
    )
    assert not data['artist_from_source']
    assert guard.decision(data, 1)['action'] == 'block'

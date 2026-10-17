"""Offline coverage of AMV recognition, privacy, approval and discovery."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from downtify import api, recognition
from downtify import recognition_routes as routes
from downtify.auth import ADMIN, CLIENT, rule_for
from downtify.recognition_store import RecognitionStore

VIDEO = 'abcdefghijk'
URL = f'https://www.youtube.com/watch?v={VIDEO}'


@pytest.mark.parametrize(
    'url',
    [
        URL,
        f'https://youtu.be/{VIDEO}?si=123',
        f'https://music.youtube.com/watch?v={VIDEO}&list=xyz',
        f'https://youtube.com/shorts/{VIDEO}',
    ],
)
def test_source_is_canonical_and_cannot_inject_extractor(url):
    assert recognition.youtube_source(url)['url'] == URL


@pytest.mark.parametrize(
    'url',
    [
        'http://127.0.0.1/private',
        'file:///etc/passwd',
        f'https://youtube.com.evil.test/watch?v={VIDEO}',
        f'https://youtube.com@evil.test/watch?v={VIDEO}',
        'https://youtube.com/playlist?list=123',
        'not a link',
    ],
)
def test_rejects_non_video_and_non_youtube_urls(url):
    with pytest.raises(ValueError, match='Paste'):
        recognition.youtube_source(url)


def test_cleaning_preserves_real_song_and_version():
    assert (
        recognition.clean_title('Artist - Broken [AMV] 4K (Remix)')
        == 'Artist - Broken (Remix)'
    )


def test_uploader_is_not_a_musician(monkeypatch):
    queries = []
    monkeypatch.setattr(
        recognition.providers,
        'search_songs',
        lambda q, limit: queries.append(q) or [],
    )
    rows = recognition.text_candidates(
        {'title': 'Slime Broken AMV'},
        {
            'title': 'Slime Broken AMV',
            'channel': 'AnimeEditor',
            'uploader': 'AnimeEditor',
            'creator': 'AnimeEditor',
        },
    )
    assert rows == []
    assert queries == ['Slime Broken']


def test_music_metadata_and_description_are_search_hints(monkeypatch):
    queries = []
    monkeypatch.setattr(
        recognition.providers,
        'search_songs',
        lambda q, limit: queries.append(q) or [],
    )
    rows = recognition.text_candidates(
        {'title': 'Slime AMV'},
        {
            'track': 'Broken',
            'artist': 'Real Musician',
            'description': 'Music: Other Artist - Another Song\nEdited by me',
        },
    )
    assert rows[0]['artist'] == 'Real Musician'
    assert 'Other Artist - Another Song' in queries


def test_dedup_retains_audio_evidence_without_fake_confidence():
    rows = recognition.merge_candidates([
        {'title': 'Broken', 'artist': 'Artist', 'method': 'audd'},
        {
            'title': 'BROKEN',
            'artist': 'Artist',
            'method': 'text-search',
            'video_id': VIDEO,
        },
    ])
    assert len(rows) == 1
    assert rows[0]['methods'] == ['audd', 'text-search']
    assert rows[0]['video_id'] == VIDEO
    assert 'confidence' not in rows[0]


def test_audio_is_opt_in_and_signed_urls_never_persist(monkeypatch):
    monkeypatch.setattr(
        recognition.stream,
        'video_info_for_recognition',
        lambda *a, **kw: {
            'title': 'AMV',
            'channel': 'Editor',
            'url': 'https://rr.googlevideo.com/audio?secret=signed',
            'duration': 180,
        },
    )
    monkeypatch.setattr(
        recognition, 'capabilities', lambda: {'audd': True, 'acoustid': False}
    )
    monkeypatch.setattr(
        recognition,
        'audd_candidates',
        lambda *a: pytest.fail('audio not enabled'),
    )
    monkeypatch.setattr(
        recognition.providers, 'search_songs', lambda *a, **kw: []
    )
    result = recognition.recognize(recognition.youtube_source(URL))
    assert result['source']['channel'] == 'Editor'
    assert 'signed' not in str(result)


def test_missing_provider_is_recoverable(monkeypatch):
    monkeypatch.setattr(
        recognition, 'capabilities', lambda: {'audd': False, 'acoustid': False}
    )
    monkeypatch.setattr(
        recognition.providers, 'search_songs', lambda *a, **kw: []
    )
    result = recognition.recognize({'title': 'AMV'}, use_audio=True)
    assert result['warnings']
    assert result['candidates'] == []


def test_audd_uses_short_local_samples_not_source_url(monkeypatch, tmp_path):
    offsets, uploads = [], []

    def sample(audio, offset, target):
        offsets.append(offset)
        target.write_bytes(b'audio')

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        @staticmethod
        def post(url, *, data, files):
            uploads.append(files['file'][1].read())
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {
                    'status': 'success',
                    'result': {'artist': 'Real Musician', 'title': 'Broken'},
                },
            )

    monkeypatch.setattr(recognition, 'sample_audio', sample)
    monkeypatch.setattr(recognition.httpx, 'Client', Client)
    result = recognition.audd_candidates(
        str(tmp_path / 'audio.mp3'), 180, 'secret'
    )
    assert offsets == [30, 90, 150]
    assert uploads == [b'audio'] * 3
    assert result[0]['method'] == 'audd'


def test_store_restart_and_per_user_cache(tmp_path):
    path = tmp_path / 'library.db'
    db = RecognitionStore(path)
    source = recognition.youtube_source(URL)
    job, created = db.create(source, 1)
    assert created
    assert db.create(source, 1)[1] is False
    assert db.get(job['id'], 2) is None
    assert db.create(source, 2)[1] is True
    reopened = RecognitionStore(path)
    assert reopened.get(job['id'], 1)['status'] == 'interrupted'
    assert reopened.create(source, 1, retry=True)[1]


def test_failed_reanalysis_does_not_erase_approval(tmp_path):
    db = RecognitionStore(tmp_path / 'library.db')
    source = recognition.youtube_source(URL)
    job, _ = db.create(source, 1)
    db.update(
        job['id'],
        1,
        'accepted',
        selected={'artist': 'Real Artist', 'title': 'Broken'},
    )
    second, _ = db.create(source, 1, retry=True)
    db.update(second['id'], 1, 'rejected')
    assert db.approved(source['key'], 1)['id'] == job['id']
    db.update(
        second['id'],
        1,
        'accepted',
        selected={'artist': 'Other', 'title': 'Other'},
    )
    assert db.get(job['id'], 1)['status'] == 'superseded'
    assert db.approved(source['key'], 1)['id'] == second['id']


def test_queue_has_per_user_limit(tmp_path):
    db = RecognitionStore(tmp_path / 'library.db')
    for index in range(10):
        db.create({'key': str(index)}, 1)
    with pytest.raises(ValueError, match='queue is full'):
        db.create({'key': 'overflow'}, 1)
    assert db.create({'key': 'another user'}, 2)[1]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(
        api.state, 'recognition', RecognitionStore(tmp_path / 'library.db')
    )
    monkeypatch.setattr(api.state, 'recognition_semaphore', None)
    monkeypatch.setattr(api.state, 'background_tasks', set())
    monkeypatch.setattr(
        recognition,
        'recognize',
        lambda source, **kwargs: {
            'source': {**source, 'title': 'Slime AMV', 'channel': 'Editor'},
            'candidates': [
                {
                    'artist': 'Actual Artist',
                    'title': 'Broken',
                    'method': 'audd',
                    'video_id': VIDEO,
                }
            ],
            'warnings': [],
        },
    )
    app = FastAPI()

    @app.middleware('http')
    async def principal(request, call_next):
        request.state.principal = SimpleNamespace(
            user_id=int(request.headers.get('x-user', '1')), is_admin=False
        )
        return await call_next(request)

    app.include_router(routes.router)
    with TestClient(app) as http:
        yield http


def _ready(client):
    response = client.post('/api/music/recognize', json={'url': URL})
    assert response.status_code == 202
    job_id = response.json()['id']
    for _ in range(50):
        job = client.get(f'/api/music/recognition/{job_id}').json()
        if job['status'] not in {'queued', 'running'}:
            return job
        time.sleep(0.01)
    pytest.fail('offline worker did not complete')


def test_complete_recognition_approval_recommendation_flow(
    client, monkeypatch
):
    job = _ready(client)
    assert job['status'] == 'review'
    assert api.state.recognition.approved(job['source']['key'], 1) is None
    response = client.patch(
        f'/api/music/recognition/{job["id"]}',
        json={'action': 'accept', 'candidate_index': 0},
    )
    assert response.status_code == 200
    called = []
    monkeypatch.setattr(
        api.providers,
        'youtube_similar_tracks',
        lambda a, t, limit: (
            called.append((a, t)) or {'artist': a, 'track': t, 'tracks': []}
        ),
    )
    response = client.get(
        '/api/music/similar',
        params={'url': URL, 'artist': 'Editor', 'track': 'Slime AMV'},
    )
    assert response.status_code == 200
    assert called == [('Actual Artist', 'Broken')]
    assert response.json()['recognition']['status'] == 'accepted'


def test_foreign_jobs_cannot_be_read_or_approved(client):
    job = _ready(client)
    url = f'/api/music/recognition/{job["id"]}'
    assert client.get(url, headers={'x-user': '2'}).status_code == 404
    assert (
        client.patch(
            url,
            headers={'x-user': '2'},
            json={'action': 'accept', 'candidate_index': 0},
        ).status_code
        == 404
    )
    assert (
        client.get('/api/music/recognition', headers={'x-user': '2'}).json()
        == []
    )


def test_manual_edit_drops_mismatched_video_identity(client):
    job = _ready(client)
    response = client.patch(
        f'/api/music/recognition/{job["id"]}',
        json={
            'action': 'accept',
            'candidate_index': 0,
            'artist': 'Different Artist',
            'title': 'Different Song',
        },
    )
    assert response.json()['selected'] == {
        'method': 'manual',
        'artist': 'Different Artist',
        'title': 'Different Song',
    }


def test_reject_leaves_no_identity(client):
    job = _ready(client)
    response = client.patch(
        f'/api/music/recognition/{job["id"]}', json={'action': 'reject'}
    )
    assert response.status_code == 200
    assert client.get('/api/music/identity', params={'url': URL}).json() == {
        'recognition': None
    }


def test_invalid_input_and_candidate_fail_closed(client):
    assert (
        client.post(
            '/api/music/recognize', json={'url': 'http://localhost/secret'}
        ).status_code
        == 400
    )
    assert client.post('/api/music/recognize', json={}).status_code == 400
    job = _ready(client)
    assert (
        client.patch(
            f'/api/music/recognition/{job["id"]}',
            json={'action': 'accept', 'candidate_index': 19},
        ).status_code
        == 400
    )


def test_library_access_is_checked_before_reading_tags(
    client, monkeypatch, tmp_path
):
    path = tmp_path / 'secret.mp3'
    path.write_bytes(b'do not read')
    monkeypatch.setattr(api, 'resolve_library_file', lambda *a: path)
    monkeypatch.setattr(api, 'allowed_entry_keys', lambda *a: {'owned'})
    monkeypatch.setattr(api, 'stored_content_key', lambda *a: 'foreign')
    monkeypatch.setattr(
        api, 'read_audio_metadata', lambda *a: pytest.fail('foreign tags read')
    )
    assert (
        client.post(
            '/api/music/recognize', json={'file': 'secret.mp3'}
        ).status_code
        == 404
    )


def test_worker_errors_do_not_leak_credentials(client, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('https://provider/?api_token=SECRET')

    monkeypatch.setattr(recognition, 'recognize', fail)
    job = _ready(client)
    assert job['status'] == 'error'
    assert 'SECRET' not in str(job)


def test_paired_client_rules_include_recognition_but_tag_writes_need_admin():
    assert rule_for('POST', '/api/music/recognize') == CLIENT
    assert rule_for('PATCH', f'/api/music/recognition/{"a" * 32}') == CLIENT
    assert rule_for('POST', '/api/music/library/scan') == CLIENT
    assert rule_for('POST', f'/api/music/recognition/{"a" * 32}/tags') == ADMIN


def test_scan_checkpoints_skip_processed_tracks(client, monkeypatch, tmp_path):
    files = []
    for index in range(12):
        path = tmp_path / f'{index}.mp3'
        path.write_bytes(b'fake')
        files.append({'file': path.name})
    monkeypatch.setattr(routes, 'list_library_entries', lambda *a, **kw: files)
    monkeypatch.setattr(
        api, 'resolve_library_file', lambda file, ctx: tmp_path / file
    )
    monkeypatch.setattr(api, 'allowed_entry_keys', lambda *a: None)
    monkeypatch.setattr(
        api, 'read_audio_metadata', lambda *a: {'title': 'AMV'}
    )
    first = client.post('/api/music/library/scan', json={})
    assert first.status_code == 202
    assert len(first.json()['jobs']) == 10
    assert first.json()['remaining'] == 2
    for _ in range(50):
        if not any(
            job['status'] in {'queued', 'running'}
            for job in client.get('/api/music/recognition').json()
        ):
            break
        time.sleep(0.01)
    second = client.post('/api/music/library/scan', json={}).json()
    assert len(second['jobs']) == 2
    assert second['remaining'] == 0


def test_non_admin_cannot_write_shared_file_tags(client):
    job = _ready(client)
    response = client.post(
        f'/api/music/recognition/{job["id"]}/tags', json={'action': 'apply'}
    )
    assert response.status_code == 403

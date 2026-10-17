"""AMV source → candidates. Never infer the musician from the uploader."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from . import providers, stream

_VIDEO = re.compile(r'^[A-Za-z0-9_-]{11}$')
_NOISE = re.compile(
    r'\b(?:amv|anime\s+mix|anime\s+music\s+video|4k|1080p|720p|hd)\b',
    re.IGNORECASE,
)
_LABEL = re.compile(
    r'^\s*(?:song|music|track|audio)\s*[:：-]\s*(.{1,200})$',
    re.IGNORECASE | re.MULTILINE,
)
SAMPLE_SECONDS = 12


def youtube_source(url: str) -> dict[str, Any]:
    """Accept only a single YouTube video; strip tracking and other URLs."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {'http', 'https'} or parsed.username:
        raise ValueError('Paste a YouTube or YouTube Music video link')
    host = (parsed.hostname or '').lower()
    if host == 'youtu.be':
        video_id = parsed.path.strip('/')
    elif host in {
        'youtube.com',
        'www.youtube.com',
        'music.youtube.com',
        'm.youtube.com',
    }:
        if parsed.path == '/watch':
            video_id = parse_qs(parsed.query).get('v', [''])[0]
        elif parsed.path.startswith(('/shorts/', '/embed/')):
            video_id = parsed.path.split('/')[2]
        else:
            video_id = ''
    else:
        video_id = ''
    if not _VIDEO.fullmatch(video_id):
        raise ValueError('Paste a link to one YouTube video, not a playlist')
    return {
        'key': f'youtube:{video_id}',
        'video_id': video_id,
        'url': f'https://www.youtube.com/watch?v={video_id}',
        'title': '',
        'channel': '',
        'file': '',
    }


def clean_title(title: str) -> str:
    """Remove technical labels without erasing remix/version information."""
    text = _NOISE.sub('', title)
    text = re.sub(r'\[\s*\]|\(\s*\)|【\s*】', '', text)
    return re.sub(r'\s+', ' ', text).strip(' -–—|')


def candidate(
    title: str, artist: str, method: str, **extra: Any
) -> dict[str, Any] | None:
    title, artist = str(title or '').strip(), str(artist or '').strip()
    if not title or not artist:
        return None
    return {
        'title': title[:200],
        'artist': artist[:200],
        'method': method,
        **extra,
    }


def merge_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Audio evidence wins over text search; keep corroborating methods."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row['artist'].casefold(), row['title'].casefold())
        if key in merged:
            found = merged[key]
            found['methods'] = list(
                dict.fromkeys(found['methods'] + [row['method']])
            )
            if not found.get('video_id') and row.get('video_id'):
                found['video_id'] = row['video_id']
        else:
            merged[key] = {**row, 'methods': [row['method']]}
    return list(merged.values())[:20]


def text_candidates(
    source: dict[str, Any], info: dict[str, Any]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    queries: list[str] = []
    # These fields describe music, unlike channel/uploader/creator.
    direct = candidate(
        info.get('track'), info.get('artist'), 'youtube-metadata'
    )
    if direct:
        rows.append(direct)
        queries.append(f'{direct["artist"]} {direct["title"]}')
    queries.extend(
        _LABEL.findall(str(info.get('description') or '')[:30000])[:2]
    )
    title = clean_title(str(source.get('title') or ''))
    if title:
        queries.append(title)
    for query in list(dict.fromkeys(queries))[:3]:
        for song in providers.search_songs(query, limit=4)[:4]:
            row = candidate(
                song.get('name'),
                ', '.join(song.get('artists') or []),
                'text-search',
                video_id=str(song.get('song_id') or ''),
                album=str(song.get('album_name') or ''),
            )
            if row:
                rows.append(row)
    return rows


def sample_audio(audio: str, offset: int, target: Path) -> None:
    """Bounded local/YouTube sample; ffmpeg never receives a user URL."""
    cmd = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y']
    if audio.startswith('https://'):
        cmd += [
            '-rw_timeout',
            '15000000',
            '-protocol_whitelist',
            'https,tls,tcp,crypto',
        ]
    cmd += [
        '-ss',
        str(offset),
        '-i',
        audio,
        '-t',
        str(SAMPLE_SECONDS),
        '-vn',
        '-ac',
        '1',
        '-ar',
        '16000',
        '-c:a',
        'pcm_s16le',
        str(target),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=40, check=False)
    if (
        result.returncode
        or not target.exists()
        or target.stat().st_size < 1000
    ):
        raise ValueError('Could not extract an audio sample')


def audd_candidates(
    audio: str, duration: float, token: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offsets = list(
        dict.fromkeys(
            max(0, min(offset, int(duration) - SAMPLE_SECONDS))
            if duration > 0
            else offset
            for offset in (30, 90, 150)
        )
    )
    with tempfile.TemporaryDirectory(prefix='downtify-recognition-') as temp:
        target = Path(temp) / 'sample.wav'
        with httpx.Client(timeout=25) as client:
            for offset in offsets:
                sample_audio(audio, offset, target)
                with target.open('rb') as clip:
                    response = client.post(
                        'https://api.audd.io/',
                        data={'api_token': token},
                        files={'file': ('sample.wav', clip, 'audio/wav')},
                    )
                response.raise_for_status()
                data = response.json()
                if data.get('status') != 'success':
                    raise ValueError(
                        'AudD refused recognition; check the server token and quota'
                    )
                result = data.get('result') or {}
                row = candidate(
                    result.get('title'),
                    result.get('artist'),
                    'audd',
                    offset=offset,
                )
                if row:
                    rows.append(row)
    return rows


def acoustid_candidates(path: Path, token: str) -> list[dict[str, Any]]:
    """Chromaprint lookup for local files; scores are provider scores only."""
    result = subprocess.run(
        ['fpcalc', '-json', '-length', '120', str(path)],
        capture_output=True,
        timeout=45,
        check=False,
    )
    if result.returncode:
        raise ValueError('Chromaprint could not fingerprint this file')
    fingerprint = json.loads(result.stdout)
    response = httpx.post(
        'https://api.acoustid.org/v2/lookup',
        timeout=25,
        data={
            'client': token,
            'duration': str(int(fingerprint['duration'])),
            'fingerprint': fingerprint['fingerprint'],
            'meta': 'recordings',
            'format': 'json',
        },
    )
    response.raise_for_status()
    data = response.json()
    if data.get('status') != 'ok':
        raise ValueError('AcoustID refused recognition; check the server key')
    rows = []
    for match in data.get('results', [])[:5]:
        for recording in match.get('recordings', [])[:3]:
            artist = ', '.join(
                a.get('name', '') for a in recording.get('artists', [])
            )
            row = candidate(
                recording.get('title'),
                artist,
                'acoustid',
                mbid=recording.get('id', ''),
                provider_score=match.get('score'),
            )
            if row:
                rows.append(row)
    return rows


def capabilities() -> dict[str, bool]:
    """Expose availability, never credentials."""
    return {
        'audd': bool(os.getenv('DOWNTIFY_AUDD_TOKEN'))
        and bool(shutil.which('ffmpeg')),
        'acoustid': bool(os.getenv('DOWNTIFY_ACOUSTID_KEY'))
        and bool(shutil.which('fpcalc')),
    }


def recognize(
    source: dict[str, Any],
    *,
    path: Path | None = None,
    cookies_file: str = '',
    use_audio: bool = False,
) -> dict[str, Any]:
    """Collect suggestions without auto-approval or modifying any audio."""
    source = dict(source)
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    info: dict[str, Any] = {}
    if source.get('video_id'):
        try:
            info = stream.video_info_for_recognition(
                source['video_id'], cookies_file=cookies_file
            )
            source.update(
                title=str(info.get('title') or '')[:500],
                channel=str(info.get('channel') or '')[:200],
            )
        except Exception:
            warnings.append(
                'YouTube metadata unavailable; retry or enter the song manually'
            )
    duration = float(info.get('duration') or source.get('duration') or 0)
    available = capabilities()
    if use_audio:
        if path is not None and available['acoustid']:
            try:
                rows.extend(
                    acoustid_candidates(
                        path, os.environ['DOWNTIFY_ACOUSTID_KEY']
                    )
                )
            except Exception:
                warnings.append(
                    'AcoustID lookup failed; check the server key and fpcalc'
                )
        audio = str(path) if path is not None else str(info.get('url') or '')
        if path is None and audio:
            parsed = urlparse(audio)
            if parsed.scheme != 'https' or not (
                parsed.hostname or ''
            ).endswith('.googlevideo.com'):
                audio = ''
        if available['audd'] and audio:
            try:
                rows.extend(
                    audd_candidates(
                        audio, duration, os.environ['DOWNTIFY_AUDD_TOKEN']
                    )
                )
            except Exception:
                warnings.append(
                    'AudD recognition failed; check the token, quota and audio access'
                )
        if not available['audd'] and not (
            path is not None and available['acoustid']
        ):
            warnings.append(
                'Audio recognition is not configured; metadata suggestions are available'
            )
    try:
        rows.extend(text_candidates(source, info))
    except Exception:
        warnings.append(
            'YouTube Music search unavailable; audio results and manual correction still work'
        )
    return {
        'source': source,
        'candidates': merge_candidates(rows),
        'warnings': warnings,
    }

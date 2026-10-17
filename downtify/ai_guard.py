"""Evidence-based AI music controls; absence of evidence is not a guarantee."""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import re
import threading
import time
from typing import Any, Iterator

from . import recognition, stream
from .ai_guard_store import AIGuardStore

RULE_VERSION = '1.0'
_CONTEXT: contextvars.ContextVar[tuple[int, str, str]] = (
    contextvars.ContextVar('music_guard', default=(0, '', ''))
)
_HINT = re.compile(
    r'\b(?:ai[ -]?(?:generated|cover|music|song|voice)|generated\s+(?:by|with|using)\s+(?:ai|suno|udio)|suno|udio|klon(?:owany|owanie)\s+głos|wygenerowan\w*\s+przez\s+(?:ai|sztuczn))\b',
    re.I,
)
_DECLARATION = re.compile(
    r'\b(?:(?:this|the|all|my|our)\s+(?:song|music|track|vocals?|songs|tracks)\s+(?:is|are|was|were|has been)\s+(?:fully\s+|entirely\s+)?(?:ai[ -]generated|generated\s+(?:by|with|using)\s+(?:ai|suno|udio))|(?:created|made|generated)\s+(?:entirely\s+|fully\s+)?(?:with|using|by)\s+(?:suno|udio))\b',
    re.I,
)
_COVER_DECLARATION = re.compile(
    r'\bthis\s+(?:is|song\s+is|track\s+is)\s+(?:an?\s+)?ai[ -]?cover\b', re.I
)
_COVER = re.compile(
    r'\b(?:ai[ -]?cover|ai[ -]?(?:voice|vocal)|voice\s+clon(?:e|ed|ing))\b',
    re.I,
)
_ASSISTED = re.compile(
    r'\b(?:ai[ -]?(?:mastered|mastering|assisted)|mastered\s+(?:by|with|using)\s+ai)\b',
    re.I,
)
_NEGATION = re.compile(
    r'\b(?:not|never|without|no)\b.{0,35}\b(?:ai|suno|udio)\b|\b(?:anti-ai|against ai)\b',
    re.I,
)


def normalized(value: Any) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip().casefold()


def artist_name(song: dict[str, Any]) -> str:
    artists = song.get('artists')
    if isinstance(artists, list) and artists:
        first = artists[0]
        return str(first.get('name', '') if isinstance(first, dict) else first)
    return str(song.get('artist') or '')


def source_key(song: dict[str, Any], video_id: str = '') -> str:
    if video_id:
        return f'youtube:{stream.clean_video_id(video_id)}'
    try:
        return recognition.youtube_source(str(song.get('url') or ''))['key']
    except ValueError:
        pass
    sid = str(song.get('song_id') or '')
    if song.get('source') == 'youtube' and re.fullmatch(r'[\w-]{11}', sid):
        return f'youtube:{sid}'
    if sid:
        source = str(song.get('source') or 'catalog')
        if source not in {
            'youtube',
            'spotify',
            'deezer',
            'slskd',
            'library',
            'catalog',
        }:
            source = 'catalog'
        return f'{source}:{sid}'
    # Versions remain distinct: do not strip "AI cover", remix or singer.
    text = '|'.join((
        normalized(artist_name(song)),
        normalized(song.get('name') or song.get('title')),
        normalized(song.get('album_name')),
    ))
    return 'metadata:' + hashlib.sha256(text.encode()).hexdigest()


def assessment_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def classify(
    song: dict[str, Any], key: str, info: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Only extractor-provided self-declarations can be confirmed automatically."""
    info = info or {}
    title = str(
        info.get('title') or song.get('name') or song.get('title') or ''
    )
    # Client descriptions are hints, never trusted first-party declarations.
    description = str(info.get('description') or '')[:30000]
    hints = str(song.get('description') or '')[:10000]
    evidence = []
    status, kind = 'unknown', 'unknown'
    kinds: set[str] = set()
    for field, text, trusted in [
        ('title', title, False),
        ('description', description, True),
        ('metadata_hint', hints, False),
    ]:
        for sentence in re.split(r'[\n.!?]+', text):
            if _NEGATION.search(sentence):
                continue
            cover, assisted = (
                bool(_COVER.search(sentence)),
                bool(_ASSISTED.search(sentence)),
            )
            declaration = (
                (
                    _DECLARATION.search(sentence)
                    or _COVER_DECLARATION.search(sentence)
                )
                if trusted
                else None
            )
            signal = (
                _HINT.search(sentence)
                or _COVER.search(sentence)
                or declaration
                or (sentence if assisted else None)
            )
            if not signal:
                continue
            found = 'confirmed_ai' if declaration else 'suspected_ai'
            if status != 'confirmed_ai':
                status = found
            kind = (
                'ai_cover'
                if cover
                else 'assisted'
                if assisted
                else 'generated'
            )
            kinds.add(kind)
            evidence.append({
                'type': 'self_declaration' if declaration else 'metadata_hint',
                'field': field,
                'text': sentence.strip()[:300],
                'source_url': f'https://www.youtube.com/watch?v={key[8:]}'
                if key.startswith('youtube:')
                else '',
                'strength': 'strong' if declaration else 'weak',
            })
    if not evidence and description:
        status = 'no_ai_evidence'
    now = time.time()
    return {
        'id': assessment_id(key),
        'source_key': key,
        'source_video_id': key[8:] if key.startswith('youtube:') else '',
        'audio_version_id': key,
        'status': status,
        'kind': 'ai_cover'
        if 'ai_cover' in kinds
        else 'generated'
        if 'generated' in kinds
        else kind,
        'kinds': sorted(kinds),
        'title': title[:300],
        'artist': str(info.get('artist') or artist_name(song))[:200],
        'artist_from_source': bool(info.get('artist'))
        or not key.startswith('youtube:'),
        'channel': str(info.get('channel_id') or info.get('channel') or '')[
            :200
        ],
        'evidence': evidence,
        'rule_version': RULE_VERSION,
        'checked_at': now,
        'expires_at': now + (86400 if status == 'unknown' else 604800),
        'metadata_checked': bool(info),
        'explanation': 'No evidence is not proof of human authorship.'
        if not evidence
        else 'Classification uses the evidence listed below; metadata may be wrong.',
    }


class MusicGuardBlocked(ValueError):
    def __init__(
        self, assessment: dict[str, Any], request_key: str = ''
    ) -> None:
        self.assessment = assessment
        self.request_key = request_key or assessment['source_key']
        super().__init__(
            f'AI Music Guard: {assessment["status"]} — review required for {assessment["title"]}'
        )

    def detail(self) -> dict[str, Any]:
        return {
            'code': 'ai_guard',
            'message': str(self),
            'assessment': self.assessment,
            'request_key': self.request_key,
        }


class AIGuard:
    def __init__(self, store: AIGuardStore) -> None:
        self.store = store
        self.metadata_lock = threading.Lock()
        self.last_lookup = 0.0
        self.cookies: Any = None

    @staticmethod
    @contextlib.contextmanager
    def context(user_id: int, song: dict[str, Any]) -> Iterator[None]:
        token = _CONTEXT.set((
            user_id,
            source_key(song),
            str(song.get('ai_confirmation') or ''),
        ))
        try:
            yield
        finally:
            _CONTEXT.reset(token)

    def assess(
        self,
        song: dict[str, Any],
        user_id: int = 0,
        *,
        video_id: str = '',
        fetch: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        key = source_key(song, video_id)
        if song.get('source') == 'library':
            key = f'private-library:{user_id}:{key}'
        cached = self.store.assessment(assessment_id(key), user_id)
        if (
            cached
            and cached['rule_version'] == RULE_VERSION
            and not force
            and (not fetch or cached.get('metadata_attempted'))
        ):
            return self.decision(cached, user_id)
        info = None
        if fetch and key.startswith('youtube:'):
            # At most two live lookups per second across this process.
            with self.metadata_lock:
                delay = 0.5 - (time.monotonic() - self.last_lookup)
                if delay > 0:
                    time.sleep(delay)
                self.last_lookup = time.monotonic()
                try:
                    info = stream.video_info_for_recognition(
                        key[8:],
                        cookies_file=stream.resolve_cookies_file(self.cookies),
                    )
                except Exception:
                    info = None
        data = classify(song, key, info)
        data['metadata_attempted'] = fetch
        if cached and cached.get('metadata_checked') and info is None:
            # An outage cannot downgrade a known confirmed/suspected result.
            data = cached
        self.store.save(data, user_id)
        return self.decision(data, user_id)

    def decision(self, data: dict[str, Any], user_id: int) -> dict[str, Any]:
        data = dict(data)
        targets = {
            'track': data['id'],
            'artist': normalized(data['artist']),
            'channel': normalized(data.get('channel')),
        }
        overrides = self.store.overrides(user_id)
        match = next(
            (
                row
                for scope in ('track', 'channel', 'artist')
                for row in overrides
                if row['scope'] == scope
                and row['target']
                and row['target'] == targets[scope]
                and not (
                    scope == 'artist'
                    and row['decision'] == 'allow'
                    and (
                        data['kind'] == 'ai_cover'
                        or not data.get('artist_from_source', True)
                    )
                )
            ),
            None,
        )
        if match:
            data['override'] = match
            data['status'] = (
                'user_allowed'
                if match['decision'] == 'allow'
                else 'confirmed_ai'
            )
            data['evidence'] = [
                *data['evidence'],
                {
                    'type': 'user_override',
                    'text': match['note'] or match['decision'],
                    'strength': 'manual',
                    'source_url': '',
                },
            ]
        policy = self.store.policy()
        status = data['status']
        exempt_kinds = set()
        if not policy['block_assisted']:
            exempt_kinds.add('assisted')
        if not policy['block_ai_covers']:
            exempt_kinds.add('ai_cover')
        kinds = set(data.get('kinds') or [data['kind']])
        exempt = bool(kinds) and kinds <= exempt_kinds
        blocked = status == 'confirmed_ai' and (
            not exempt or bool(match and match['decision'] == 'block')
        )
        confirm = (
            status == 'suspected_ai' and policy['mode'] != 'gentle'
        ) or (
            status in {'unknown', 'no_ai_evidence'}
            and policy['mode'] == 'strict'
        )
        data['action'] = (
            'block'
            if blocked
            else 'confirm'
            if confirm and not exempt
            else 'allow'
        )
        if not policy['enabled'] or not policy['check_downloads']:
            data['action'] = 'allow'
        return data

    def preflight(self, song: dict[str, Any], user_id: int) -> dict[str, Any]:
        data = self.assess(song, user_id)
        version = self.store.resolved(source_key(song))
        if version:
            cached = self.store.assessment(
                assessment_id(version), user_id, fresh=False
            )
            if cached:
                actual = self.decision(cached, user_id)
                if actual['action'] != 'allow':
                    data = actual
        if data['action'] != 'allow' and not self.store.check_grant(
            str(song.get('ai_confirmation') or ''), user_id, source_key(song)
        ):
            raise MusicGuardBlocked(data, source_key(song))
        return data

    def preflight_context(self, song: dict[str, Any]) -> None:
        user_id, _, _ = _CONTEXT.get()
        self.preflight(song, user_id)

    def enforce(self, song: dict[str, Any], video_id: str = '') -> None:
        policy = self.store.policy()
        if not policy['enabled'] or not policy['check_downloads']:
            return
        user_id, request_key, grant = _CONTEXT.get()
        key = source_key(song, video_id)
        data = self.assess(
            song, user_id, video_id=video_id, fetch=bool(video_id)
        )
        if request_key and video_id:
            self.store.record_resolved(request_key, key)
        if data['action'] == 'allow':
            self.store.check_grant(
                grant,
                user_id,
                request_key or source_key(song),
                key,
                consume=True,
            )
            return
        if self.store.check_grant(
            grant, user_id, request_key or source_key(song), key, consume=True
        ):
            return
        raise MusicGuardBlocked(data, request_key or source_key(song))

    def enforce_existing(self, song: dict[str, Any], user_id: int) -> None:
        data = self.preflight(song, user_id)
        if data['action'] != 'allow' and not self.store.check_grant(
            str(song.get('ai_confirmation') or ''),
            user_id,
            source_key(song),
            data['source_key'],
            consume=True,
        ):
            raise MusicGuardBlocked(data, source_key(song))

    def shareable(self, song: dict[str, Any], users: set[int]) -> set[int]:
        allowed = set()
        for user in users:
            try:
                self.enforce_existing(song, user)
                allowed.add(user)
            except MusicGuardBlocked:
                pass
        return allowed

    def filter_rows(
        self, rows: list[Any], user_id: int, *, kind: str = 'track'
    ) -> tuple[list[Any], int]:
        policy = self.store.policy()
        kept, removed = [], 0
        for row in rows:
            if not isinstance(row, dict):
                kept.append(row)
                continue
            song = dict(row)
            if kind == 'artists':
                song.update(artist=row.get('name') or '', title='', name='')
            data = self.assess(song, user_id)
            hide = policy['enabled'] and (
                (policy['hide_confirmed'] and data['status'] == 'confirmed_ai')
                or (
                    policy['hide_suspected']
                    and data['status'] == 'suspected_ai'
                )
            )
            if hide:
                removed += 1
            else:
                kept.append({**row, 'ai_guard': data})
        return kept, removed

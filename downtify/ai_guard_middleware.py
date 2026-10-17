"""Annotate and filter discovery before a client can display the results."""

from __future__ import annotations

import json
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import api

PATHS = {
    '/api/discover',
    '/api/discover/chart',
    '/api/similar/tracks',
    '/api/music/similar',
    '/api/discover/collections',
    '/api/discover/collections/deezer',
    '/api/discover/collections/spotify',
    '/api/songs/search',
    '/api/artists/similar',
}


def filter_result(data: Any, user_id: int) -> Any:
    engine = api.state.ai_guard
    if engine is None:
        return data
    if isinstance(data, list):
        return engine.filter_rows(data, user_id)[0]
    if not isinstance(data, dict):
        return data
    result = dict(data)
    removed = 0
    for key in ('tracks', 'artists', 'albums', 'more_albums', 'playlists'):
        if isinstance(result.get(key), list):
            result[key], count = engine.filter_rows(
                result[key], user_id, kind=key
            )
            removed += count
    result['ai_guard_summary'] = {
        'hidden': removed,
        'absence_of_evidence_is_not_proof': True,
    }
    return result


class AIGuardMiddleware(BaseHTTPMiddleware):
    filter_result = staticmethod(filter_result)

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        if (
            request.url.path not in PATHS
            or response.status_code != 200
            or api.state.ai_guard is None
        ):
            return response
        # Added inside GZip middleware, so these bytes are ordinary JSON.
        body = b''.join([chunk async for chunk in response.body_iterator])
        try:
            data = json.loads(body)
            filtered = await api.asyncio.to_thread(
                self.filter_result, data, api.request_user_id(request)
            )
        except Exception:
            return JSONResponse(
                {
                    'detail': 'AI Music Guard could not verify discovery results; retry later'
                },
                status_code=503,
            )
        headers = {
            key: value
            for key, value in response.headers.items()
            if key not in {'content-length', 'content-encoding', 'etag'}
        }
        return JSONResponse(
            filtered, headers=headers, background=response.background
        )

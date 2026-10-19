"""Request limits: body size (security baseline S5) and request rates (S8).

Content-Length is checked up front; chunked or lying bodies are counted as they arrive, so
an oversized request is rejected without buffering it.
"""

import time
from collections import defaultdict, deque
from collections.abc import Hashable
from typing import Literal

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agentforge.core.settings import Settings

UPLOAD_PATH_SUFFIXES = ("/documents", "/questionnaires")

RateBucket = Literal["uploads", "answers", "runs"]


class SlidingWindow:
    """At most `limit` events per key in any 60-second window (in-process: per API replica)."""

    def __init__(self, limit: int, window: float = 60.0) -> None:
        self.limit, self.window = limit, window
        self._events: dict[Hashable, deque[float]] = defaultdict(deque)

    def allow(self, key: Hashable) -> bool:
        now = time.monotonic()
        events = self._events[key]
        while events and now - events[0] >= self.window:
            events.popleft()
        if len(events) >= self.limit:
            return False
        events.append(now)
        return True

    def retry_after(self, key: Hashable) -> int:
        events = self._events[key]
        return max(1, int(self.window - (time.monotonic() - events[0])) + 1) if events else 1


class RateLimits:
    """Per caller (a person, or an API key) limits on uploads, questions, and runs."""

    def __init__(self, settings: Settings) -> None:
        self._windows: dict[RateBucket, SlidingWindow] = {
            "uploads": SlidingWindow(settings.rate_uploads_per_minute),
            "answers": SlidingWindow(settings.rate_answers_per_minute),
            "runs": SlidingWindow(settings.rate_runs_per_minute),
        }

    def refused(self, bucket: RateBucket, caller: Hashable) -> int | None:
        """None if allowed (and counted); otherwise the seconds until the next is allowed."""
        window = self._windows[bucket]
        return None if window.allow(caller) else window.retry_after(caller)


class BodyTooLargeError(Exception):
    pass


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, default_limit: int, upload_limit: int) -> None:
        self.app = app
        self.default_limit = default_limit
        self.upload_limit = upload_limit

    def _limit_for(self, scope: Scope) -> int:
        path = str(scope.get("path", ""))
        is_upload = scope.get("method") == "POST" and path.endswith(UPLOAD_PATH_SUFFIXES)
        return self.upload_limit if is_upload else self.default_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self._limit_for(scope)
        headers = dict(scope.get("headers", []))
        declared = headers.get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await _reject(send, limit)
            return
        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLargeError
            return message

        try:
            await self.app(scope, counting_receive, send)
        except BodyTooLargeError:
            await _reject(send, limit)


async def _reject(send: Send, limit: int) -> None:
    body = (
        b'{"type":"about:blank","title":"Request body too large","status":413,'
        + f'"detail":"Limit is {limit // 1024} KB"'.encode()
        + b"}"
    )
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/problem+json"), (b"connection", b"close")],
        }
    )
    await send({"type": "http.response.body", "body": body})

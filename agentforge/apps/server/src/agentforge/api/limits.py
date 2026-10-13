"""Request body size limits, enforced while the body streams in (security baseline S5).

Content-Length is checked up front; chunked or lying bodies are counted as they arrive, so
an oversized request is rejected without buffering it.
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

UPLOAD_PATH_SUFFIX = "/documents"


class BodyTooLargeError(Exception):
    pass


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, default_limit: int, upload_limit: int) -> None:
        self.app = app
        self.default_limit = default_limit
        self.upload_limit = upload_limit

    def _limit_for(self, scope: Scope) -> int:
        path = str(scope.get("path", ""))
        is_upload = scope.get("method") == "POST" and path.endswith(UPLOAD_PATH_SUFFIX)
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

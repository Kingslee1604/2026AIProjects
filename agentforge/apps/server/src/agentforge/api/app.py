"""FastAPI application factory."""

import time
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from opentelemetry import trace
from opentelemetry.propagate import extract
from starlette.exceptions import HTTPException as StarletteHTTPException

from agentforge import __version__
from agentforge.core.logging import configure_logging, get_logger
from agentforge.core.settings import Settings, get_settings
from agentforge.core.telemetry import configure_tracing, current_trace_id

log = get_logger("agentforge.api")
tracer = trace.get_tracer("agentforge.api")

PROBLEM_JSON = "application/problem+json"


def problem(status: int, title: str, detail: str | None = None) -> JSONResponse:
    """RFC 9457 problem details, always carrying the trace ID for support and debugging."""
    body: dict[str, object] = {"type": "about:blank", "title": title, "status": status}
    if detail:
        body["detail"] = detail
    trace_id = current_trace_id()
    if trace_id:
        body["trace_id"] = trace_id
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


async def _http_error(_request: Request, exc: Exception) -> Response:
    if isinstance(exc, StarletteHTTPException):
        return problem(exc.status_code, str(exc.detail))
    return await _unhandled_error(_request, exc)


async def _validation_error(_request: Request, exc: Exception) -> Response:
    if isinstance(exc, RequestValidationError):
        return problem(422, "Request validation failed", f"{len(exc.errors())} invalid field(s)")
    return await _unhandled_error(_request, exc)


async def _unhandled_error(_request: Request, exc: Exception) -> Response:
    log.error("unhandled_error", exc_info=exc)
    return problem(500, "Internal server error")


async def _trace_and_log(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """One span per request (continuing an incoming W3C traceparent) and one access log line."""
    started = time.perf_counter()
    with tracer.start_as_current_span(
        f"{request.method} {request.url.path}", context=extract(request.headers)
    ) as span:
        try:
            response = await call_next(request)
        except Exception as exc:  # converted to a 500 problem response
            response = await _unhandled_error(request, exc)
        span.set_attribute("http.response.status_code", response.status_code)
        trace_id = current_trace_id()
        if trace_id:
            response.headers["X-Trace-Id"] = trace_id
        log.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)
    configure_tracing(settings)

    app = FastAPI(
        title="AgentForge API",
        version=__version__,
        docs_url="/docs" if settings.env != "production" else None,
        redoc_url=None,
    )
    app.middleware("http")(_trace_and_log)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"status": "ok"}

    v1 = APIRouter(prefix="/v1")

    @v1.get("/meta")
    async def meta() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"name": "agentforge", "version": __version__, "env": settings.env}

    app.include_router(v1)
    return app

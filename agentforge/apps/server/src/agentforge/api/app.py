"""FastAPI application factory."""

import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from opentelemetry import trace
from opentelemetry.propagate import extract
from starlette.exceptions import HTTPException as StarletteHTTPException

from agentforge import __version__
from agentforge.api import (
    admin_routes,
    auth_routes,
    knowledge_routes,
    qa_routes,
    questionnaire_routes,
    run_routes,
)
from agentforge.api.limits import BodySizeLimitMiddleware
from agentforge.auth.service import AuthService
from agentforge.core.logging import configure_logging, get_logger
from agentforge.core.settings import Settings, get_settings
from agentforge.core.telemetry import configure_tracing, current_trace_id
from agentforge.runs.events import EventHub
from agentforge.services import build_services

log = get_logger("agentforge.api")
tracer = trace.get_tracer("agentforge.api")

PROBLEM_JSON = "application/problem+json"


# Routes reachable without a session. Every other route must require authentication;
# tests/integration/test_auth.py enforces this for every registered route (baseline S2).
PUBLIC_ROUTES = frozenset(
    {
        ("GET", "/healthz"),
        ("GET", "/v1/meta"),
        ("POST", "/v1/auth/login"),
        ("GET", "/openapi.json"),
        ("GET", "/docs"),
        ("GET", "/docs/oauth2-redirect"),
    }
)


def problem(
    status: int,
    title: str,
    detail: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """RFC 9457 problem details, always carrying the trace ID for support and debugging."""
    body: dict[str, object] = {"type": "about:blank", "title": title, "status": status}
    if detail:
        body["detail"] = detail
    trace_id = current_trace_id()
    if trace_id:
        body["trace_id"] = trace_id
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


async def _http_error(_request: Request, exc: Exception) -> Response:
    if isinstance(exc, StarletteHTTPException):
        return problem(exc.status_code, str(exc.detail), headers=exc.headers)
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

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        if settings.database_url is None:  # API without a database: /healthz and /v1/meta only
            yield
            return
        services = build_services(settings)
        app.state.services = services
        app.state.sessions = services.sessions
        app.state.auth = AuthService(services.sessions, settings)
        hub = EventHub(settings.database_url.get_secret_value())
        await hub.start()
        app.state.event_hub = hub
        try:
            yield
        finally:
            await hub.stop()
            await services.close()

    app = FastAPI(
        title="AgentForge API",
        version=__version__,
        docs_url="/docs" if settings.env != "production" else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.auth = None
    app.state.sessions = None
    app.state.services = None
    app.state.event_hub = None
    app.middleware("http")(_trace_and_log)
    app.add_middleware(
        BodySizeLimitMiddleware,
        default_limit=settings.max_request_kb * 1024,
        upload_limit=settings.max_upload_mb * 1024 * 1024,
    )
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
    app.include_router(auth_routes.router)
    app.include_router(admin_routes.router)
    app.include_router(knowledge_routes.router)
    app.include_router(qa_routes.router)
    app.include_router(run_routes.router)
    app.include_router(questionnaire_routes.router)
    return app

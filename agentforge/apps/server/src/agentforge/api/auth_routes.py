"""/v1/auth: login, session info, logout, logout everywhere."""

import ipaddress
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from agentforge.api.deps import (
    current_principal,
    get_auth,
    get_settings_from_app,
    session_cookie_name,
)
from agentforge.auth.rbac import Principal
from agentforge.auth.service import (
    AuthService,
    InvalidCredentialsError,
    LoginThrottledError,
    hash_token,
)
from agentforge.core.settings import Settings

router = APIRouter(prefix="/v1/auth", tags=["auth"])

AuthDep = Annotated[AuthService, Depends(get_auth)]
SettingsDep = Annotated[Settings, Depends(get_settings_from_app)]
PrincipalDep = Annotated[Principal, Depends(current_principal)]


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class SessionInfo(BaseModel):
    user_id: UUID
    email: str
    org_id: UUID
    role: str
    can_review: bool
    csrf_token: str


def _session_info(principal: Principal, csrf_token: str) -> SessionInfo:
    return SessionInfo(
        user_id=principal.user_id,
        email=principal.email,
        org_id=principal.org_id,
        role=principal.role.value,
        can_review=principal.can_review,
        csrf_token=csrf_token,
    )


def _client_ip(request: Request) -> str | None:
    """The peer address, if it is a real IP (test clients and some proxies send names)."""
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None


def _clear_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        session_cookie_name(settings),
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


@router.post("/login", response_model=SessionInfo)
async def login(
    body: LoginRequest, request: Request, response: Response, auth: AuthDep, settings: SettingsDep
) -> SessionInfo:
    try:
        session = await auth.login(
            body.email,
            body.password,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except LoginThrottledError:
        retry = str(settings.login_window_minutes * 60)
        raise HTTPException(
            429,
            "Too many failed sign-in attempts. Try again later.",
            headers={"Retry-After": retry},
        ) from None
    except InvalidCredentialsError:
        raise HTTPException(401, "Invalid email or password") from None

    # Session fixation defense: a login always replaces any session the browser already had.
    previous = request.cookies.get(session_cookie_name(settings))
    if previous:
        await auth.logout(hash_token(previous))

    response.set_cookie(
        session_cookie_name(settings),
        session.token,
        max_age=settings.session_absolute_hours * 3600,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return _session_info(session.principal, session.csrf_token)


@router.get("/session", response_model=SessionInfo)
async def get_session(request: Request, principal: PrincipalDep) -> SessionInfo:
    return _session_info(principal, request.state.csrf_token)


@router.post("/logout", status_code=204)
async def logout(
    response: Response, principal: PrincipalDep, auth: AuthDep, settings: SettingsDep
) -> None:
    await auth.logout(principal.session_token_hash)
    _clear_cookie(response, settings)


@router.post("/logout-all", status_code=204)
async def logout_all(
    response: Response, principal: PrincipalDep, auth: AuthDep, settings: SettingsDep
) -> None:
    await auth.logout_everywhere(principal.user_id)
    _clear_cookie(response, settings)

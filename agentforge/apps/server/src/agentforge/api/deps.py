"""Request dependencies: authentication, CSRF, and permission checks.

Every route that is not explicitly public depends on `current_principal` (directly or through
`require`). tests/integration/test_auth.py checks this for every registered route.
"""

import ipaddress
import secrets
from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, Request

from agentforge.auth.api_keys import ApiKeyService
from agentforge.auth.rbac import Permission, Principal, authorize
from agentforge.auth.service import AuthService
from agentforge.core.settings import Settings

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_HEADER = "X-CSRF-Token"


def session_cookie_name(settings: Settings) -> str:
    # The __Host- prefix (Secure, Path=/, no Domain) is only valid over TLS.
    return "__Host-af_session" if settings.cookie_secure else "af_session"


def client_ip(request: Request) -> str | None:
    """The peer address, if it is a real IP (test clients and some proxies send names)."""
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None


def get_settings_from_app(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_auth(request: Request) -> AuthService:
    auth: AuthService | None = request.app.state.auth
    if auth is None:
        raise HTTPException(503, "Database is not configured")
    return auth


def get_api_keys(request: Request) -> ApiKeyService:
    keys: ApiKeyService | None = request.app.state.api_keys
    if keys is None:
        raise HTTPException(503, "Database is not configured")
    return keys


async def current_principal(
    request: Request,
    auth: AuthService = Depends(get_auth),  # noqa: B008 - FastAPI dependency
    settings: Settings = Depends(get_settings_from_app),  # noqa: B008
    keys: ApiKeyService = Depends(get_api_keys),  # noqa: B008
) -> Principal:
    """A signed-in session (cookie, with CSRF on unsafe methods) or an API key
    (`Authorization: Bearer af_...`: no cookie, so no CSRF). A presented key that does not
    resolve is refused; it never falls back to a cookie."""
    authorization = request.headers.get("Authorization")
    if authorization is not None:
        scheme, _, credential = authorization.partition(" ")
        if scheme.lower() != "bearer" or not credential.strip():
            raise HTTPException(401, "Use Authorization: Bearer <API key>")
        principal = await keys.resolve(credential.strip())
        if principal is None:
            raise HTTPException(401, "API key invalid, expired, or revoked")
        return principal
    token = request.cookies.get(session_cookie_name(settings))
    if not token:
        raise HTTPException(401, "Authentication required")
    resolved = await auth.resolve(token)
    if resolved is None:
        raise HTTPException(401, "Session expired or invalid")
    principal, csrf_token = resolved
    if request.method not in SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not secrets.compare_digest(sent.encode(), csrf_token.encode()):
            raise HTTPException(403, "CSRF token missing or invalid")
    request.state.csrf_token = csrf_token
    return principal


async def session_principal(principal: Principal = Depends(current_principal)) -> Principal:  # noqa: B008
    """Only a signed-in person, not an API key (session info, sign-out, key management)."""
    if principal.api_key_id is not None:
        raise HTTPException(403, "This needs a signed-in session, not an API key")
    return principal


def require(permission: Permission) -> Callable[..., Awaitable[Principal]]:
    async def dependency(principal: Principal = Depends(current_principal)) -> Principal:  # noqa: B008
        if not authorize(principal, permission):
            raise HTTPException(403, f"Missing permission: {permission.value}")
        return principal

    return dependency

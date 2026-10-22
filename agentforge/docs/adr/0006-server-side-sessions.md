# ADR-0006: Server-side sessions over browser JWTs

- **Status:** Accepted
- **Date:** 2026-10-12

## Context

The web app and the API share one origin through the Next.js `/api` proxy. Sessions must be
revocable at once: on sign-out, "sign out everywhere", a disabled user, or a password change. A
JWT in browser storage is readable by any injected script and stays valid until it expires.

## Decision

- An opaque random token in an **HttpOnly, SameSite=Lax** cookie. It is **Secure, with the
  `__Host-` prefix, over TLS**, and production refuses to start without it. The database
  stores only its SHA-256 (`auth.sessions`).
- **CSRF**: a per-session token, required in `X-CSRF-Token` on every unsafe method.
- Rotation on login, idle (120 min) and absolute (12 h) timeouts, and server-side revocation.
- Programs use **API keys** (`Authorization: Bearer af_…`, R1-42), never cookies. A key that
  is presented but invalid is refused and never falls back to a cookie.

## Consequences

- Every request does one indexed lookup; this is negligible at R1 scale.
- Revocation is immediate and tested (`test_auth.py`, `test_api_keys.py`).
- A single sign-on (OIDC) login can later create the same kind of session (R3).

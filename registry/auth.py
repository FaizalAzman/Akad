"""Bearer-token authentication for the registry API.

Tokens come from AKAD_API_TOKENS (comma-separated), read on every request so
they can be rotated by restarting with a new value. Writes always need a
valid token. Reads are open unless AKAD_REGISTRY_READS_REQUIRE_AUTH=true.
With no tokens configured, every write is rejected: the registry fails
closed rather than accepting anonymous publishes.
"""
from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException

TOKENS_ENV = "AKAD_API_TOKENS"
READS_REQUIRE_AUTH_ENV = "AKAD_REGISTRY_READS_REQUIRE_AUTH"


def _configured_tokens() -> list[str]:
    return [t.strip() for t in os.environ.get(TOKENS_ENV, "").split(",") if t.strip()]


def _reject(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def _check(authorization: str | None) -> None:
    tokens = _configured_tokens()
    if not tokens:
        raise _reject(f"The registry has no API tokens configured ({TOKENS_ENV}), so this request is refused.")
    scheme, _, presented = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not presented:
        raise _reject("Missing bearer token: send 'Authorization: Bearer <token>'.")
    # Compare against every token so timing doesn't reveal which one matched.
    matches = [hmac.compare_digest(presented.encode(), t.encode()) for t in tokens]
    if not any(matches):
        raise _reject("Invalid API token.")


def require_write_token(authorization: str | None = Header(None)) -> None:
    _check(authorization)


def require_read_access(authorization: str | None = Header(None)) -> None:
    if os.environ.get(READS_REQUIRE_AUTH_ENV, "").lower() in ("1", "true", "yes"):
        _check(authorization)

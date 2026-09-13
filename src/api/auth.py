"""Admin authentication helpers for the API."""

from __future__ import annotations

import secrets
import time
from typing import Dict

from fastapi import Header, HTTPException

from src.config import get_settings

_TOKEN_TTL_SECONDS = 8 * 60 * 60

# Accepted only when ENV is not production. See require_admin().
DEV_TOKEN = "local-dev-token"
_admin_tokens: Dict[str, float] = {}


def _purge_expired_tokens() -> None:
    now = time.time()
    expired = [token for token, expiry in _admin_tokens.items() if expiry <= now]
    for token in expired:
        _admin_tokens.pop(token, None)


def issue_admin_token() -> str:
    _purge_expired_tokens()
    token = secrets.token_urlsafe(32)
    _admin_tokens[token] = time.time() + _TOKEN_TTL_SECONDS
    return token


def revoke_admin_token(token: str) -> None:
    _admin_tokens.pop(token, None)


def validate_admin_token(token: str | None) -> bool:
    if not token:
        return False
    _purge_expired_tokens()
    return token in _admin_tokens


def require_admin(x_admin_token: str | None = Header(None)) -> str:
    settings = get_settings()

    # Local-development convenience. This MUST stay below the settings read and
    # behind the is_production check: without the guard, anyone who knows the
    # literal string gets admin on every mutating endpoint in production.
    if not settings.is_production and x_admin_token == DEV_TOKEN:
        return x_admin_token

    if not settings.admin_password:
        raise HTTPException(status_code=503, detail="Admin password not configured")
    if not validate_admin_token(x_admin_token):
        raise HTTPException(status_code=401, detail="Admin authorization required")
    return x_admin_token

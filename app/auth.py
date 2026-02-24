"""
Admin dashboard authentication.

Uses HMAC-SHA256 signed cookies — no external dependencies.

Configuration (set in Railway Variables or .env):
  DASHBOARD_USERNAME   — login username (default: "admin")
  DASHBOARD_PASSWORD   — login password (required; dashboard blocked if unset)
  DASHBOARD_SECRET     — cookie signing key (auto-generated but not persisted
                         across redeploys; set explicitly to keep sessions alive)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time

from fastapi import Request
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)

# Session token lives for 7 days
_SESSION_TTL = 86_400 * 7
_COOKIE_NAME = "nvsession"

_secret: str = ""


def _get_secret() -> str:
    """Return the HMAC signing secret, logging a warning if not persisted."""
    global _secret
    if not _secret:
        env_val = os.environ.get("DASHBOARD_SECRET", "").strip()
        if env_val:
            _secret = env_val
        else:
            _secret = secrets.token_hex(32)
            logger.warning(
                "DASHBOARD_SECRET is not set — sessions will not survive redeploys. "
                "Add DASHBOARD_SECRET=<random-hex-string> in Railway > Variables."
            )
    return _secret


def _sign(payload: str) -> str:
    return hmac.new(_get_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()


def create_session_token(username: str) -> str:
    ts = str(int(time.time()))
    payload = f"{username}:{ts}"
    return f"{payload}:{_sign(payload)}"


def verify_session_token(token: str) -> str | None:
    """Return the username if the token is valid and not expired, else None."""
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return None
        username, ts, sig = parts
        payload = f"{username}:{ts}"
        if not hmac.compare_digest(_sign(payload), sig):
            return None
        if time.time() - int(ts) > _SESSION_TTL:
            return None
        return username
    except Exception:
        return None


def check_credentials(username: str, password: str) -> bool:
    """Constant-time credential check against env-var values."""
    expected_user = os.environ.get("DASHBOARD_USERNAME", "admin")
    expected_pass = os.environ.get("DASHBOARD_PASSWORD", "")
    if not expected_pass:
        logger.warning(
            "DASHBOARD_PASSWORD is not set — dashboard login is disabled. "
            "Add DASHBOARD_PASSWORD=<your-password> in Railway > Variables."
        )
        return False
    return (
        secrets.compare_digest(username.encode(), expected_user.encode())
        and secrets.compare_digest(password.encode(), expected_pass.encode())
    )


def get_session_user(request: Request) -> str | None:
    """Return the authenticated username from the cookie, or None."""
    token = request.cookies.get(_COOKIE_NAME, "")
    return verify_session_token(token) if token else None


def require_auth(request: Request) -> RedirectResponse | None:
    """
    Call at the top of protected route handlers.
    Returns a RedirectResponse to /login if not authenticated, else None.
    """
    if not get_session_user(request):
        return RedirectResponse(url="/login", status_code=302)
    return None


def login_response(username: str, redirect_to: str = "/dashboard") -> RedirectResponse:
    """Build a redirect response that sets the session cookie."""
    response = RedirectResponse(url=redirect_to, status_code=302)
    token = create_session_token(username)
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=_SESSION_TTL,
        secure=False,  # Railway terminates TLS; cookie is already on HTTPS transport
    )
    return response


def logout_response(redirect_to: str = "/login") -> RedirectResponse:
    """Build a redirect response that clears the session cookie."""
    response = RedirectResponse(url=redirect_to, status_code=302)
    response.delete_cookie(key=_COOKIE_NAME)
    return response

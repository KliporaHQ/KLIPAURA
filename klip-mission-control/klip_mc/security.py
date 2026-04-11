"""
Mission Control authentication — optional JWT and service token.

When ``MC_AUTH_REQUIRED`` is true, mutating endpoints require either a valid
``Authorization: Bearer`` JWT (from ``POST .../auth/login``) or ``X-Service-Token``
matching ``MC_SERVICE_TOKEN`` (for automation and ``publish_event`` callers).
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Optional

from fastapi import Header, HTTPException
from jose import JWTError, jwt

_MC_AUTH_REQUIRED = ("1", "true", "yes", "on")


def mc_auth_required() -> bool:
    return (os.getenv("MC_AUTH_REQUIRED") or "").strip().lower() in _MC_AUTH_REQUIRED


def _jwt_secret() -> str:
    return (os.getenv("JWT_SECRET_KEY") or os.getenv("JWT_SECRET") or "dev-secret-change-in-production").strip()


def _jwt_algorithm() -> str:
    return (os.getenv("JWT_ALGORITHM") or "HS256").strip()


def _jwt_exp_hours() -> int:
    try:
        return int((os.getenv("JWT_EXPIRATION_HOURS") or "24").strip() or "24")
    except ValueError:
        return 24


def create_access_token(subject: str = "mc-admin") -> str:
    """Issue a short-lived JWT for dashboard or API clients."""
    exp = datetime.utcnow() + timedelta(hours=_jwt_exp_hours())
    payload = {"sub": subject, "exp": exp}
    return jwt.encode(payload, _jwt_secret(), algorithm=_jwt_algorithm())


def verify_bearer_token(authorization: Optional[str]) -> bool:
    if not authorization or not authorization.lower().startswith("bearer "):
        return False
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        return False
    try:
        jwt.decode(token, _jwt_secret(), algorithms=[_jwt_algorithm()])
        return True
    except JWTError:
        return False


def verify_service_token_header(x_service_token: Optional[str]) -> bool:
    expected = (os.getenv("MC_SERVICE_TOKEN") or "").strip()
    if not expected:
        return False
    if not x_service_token:
        return False
    return secrets.compare_digest(x_service_token.strip(), expected)


def _env_val(*keys: str) -> str:
    """First non-empty env after strip; strip UTF-8 BOM (some dashboards prepend it)."""
    for k in keys:
        raw = os.getenv(k)
        if not raw:
            continue
        v = str(raw).strip().strip("\ufeff").strip()
        if v:
            return v
    return ""


_RUNTIME_CREDS_NAME = "mc_operator_credentials.json"


def _load_runtime_operator_creds() -> dict[str, Any] | None:
    """Optional overlay written by ``PUT /api/v1/admin/credentials`` (persists under ``JOBS_DIR``)."""
    jd = (os.getenv("JOBS_DIR") or "").strip()
    if not jd:
        return None
    p = Path(jd) / _RUNTIME_CREDS_NAME
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("password"):
            return data
    except Exception:
        return None
    return None


def get_effective_operator_username() -> str:
    """Username for login UI / credential rotation (runtime file overrides env)."""
    rt = _load_runtime_operator_creds()
    if rt and rt.get("username"):
        return str(rt["username"]).strip()
    return _env_val("MC_ADMIN_USER", "ADMIN_USERNAME", "ADMIN_USER") or "klipaura2026"


def write_runtime_operator_creds(*, username: str, password: str) -> Path:
    """Atomic write of operator username/password (requires valid prior auth to call)."""
    jd = (os.getenv("JOBS_DIR") or "").strip()
    if not jd:
        raise ValueError("JOBS_DIR is not set")
    Path(jd).mkdir(parents=True, exist_ok=True)
    p = Path(jd) / _RUNTIME_CREDS_NAME
    payload = {
        "username": str(username).strip(),
        "password": str(password).strip(),
    }
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return p


def _implicit_standard_password_allowed() -> bool:
    """
    When no operator password is configured (env + runtime file), allow the AGENTS.md
    standard password only in local dev: default JWT secret and not a hosted deploy.
    Set MC_ADMIN_PASSWORD (or ADMIN_PASSWORD) in production.
    """
    rt = _load_runtime_operator_creds()
    if rt and rt.get("password"):
        return False
    if _env_val("MC_ADMIN_PASSWORD", "ADMIN_PASSWORD"):
        return False
    if (os.getenv("MC_ENV") or "").strip().lower() == "production":
        return False
    if any(
        os.getenv(k)
        for k in (
            "RAILWAY_ENVIRONMENT",
            "RAILWAY_PROJECT_ID",
            "FLY_APP_NAME",
            "VERCEL",
            "RENDER",
        )
    ):
        return False
    jwt_secret = (os.getenv("JWT_SECRET_KEY") or os.getenv("JWT_SECRET") or "dev-secret-change-in-production").strip()
    return jwt_secret == "dev-secret-change-in-production"


def _effective_operator_password() -> str:
    """Password used for login verification (runtime file > env > implicit dev only)."""
    rt = _load_runtime_operator_creds()
    if rt and rt.get("password"):
        return str(rt["password"]).strip()
    env_p = _env_val("MC_ADMIN_PASSWORD", "ADMIN_PASSWORD")
    if env_p:
        return env_p
    if _implicit_standard_password_allowed():
        return "Klipaura123"
    return ""


def login_password_configured() -> bool:
    return bool(_effective_operator_password())


def verify_login_password(password: str) -> bool:
    expected = _effective_operator_password()
    if not expected:
        return False
    got = str(password).strip().strip("\ufeff").strip()
    return secrets.compare_digest(got, expected)


def verify_login_user(user: str) -> bool:
    rt = _load_runtime_operator_creds()
    if rt and rt.get("username"):
        expected = str(rt["username"])
    else:
        expected = _env_val("MC_ADMIN_USER", "ADMIN_USERNAME", "ADMIN_USER") or "klipaura2026"
    got = str(user).strip().strip("\ufeff").strip()
    return secrets.compare_digest(got, expected)


def require_mc_operator(
    authorization: Annotated[Optional[str], Header()] = None,
    x_service_token: Annotated[Optional[str], Header()] = None,
) -> None:
    """FastAPI dependency for protected routes (sync or async)."""
    if not mc_auth_required():
        return
    if verify_service_token_header(x_service_token):
        return
    if verify_bearer_token(authorization):
        return
    raise HTTPException(status_code=401, detail="Authentication required")


def require_events_ingest(
    authorization: Annotated[Optional[str], Header()] = None,
    x_service_token: Annotated[Optional[str], Header()] = None,
    x_events_token: Annotated[Optional[str], Header(alias="X-Events-Token")] = None,
) -> None:
    """
    ``/api/events/ingest`` may use Bearer JWT, ``X-Service-Token``, or ``X-Events-Token``
    when ``MC_EVENTS_INGEST_SECRET`` is set (recommended in production).
    """
    ingest_secret = (os.getenv("MC_EVENTS_INGEST_SECRET") or "").strip()
    if not mc_auth_required() and not ingest_secret:
        return
    if ingest_secret and x_events_token and secrets.compare_digest(x_events_token.strip(), ingest_secret):
        return
    if verify_service_token_header(x_service_token):
        return
    if verify_bearer_token(authorization):
        return
    if mc_auth_required():
        raise HTTPException(status_code=401, detail="Authentication required for event ingest")
    if ingest_secret:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Events-Token")

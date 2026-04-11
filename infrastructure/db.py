from __future__ import annotations

import os
import typing as t

try:
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import Engine
except ImportError:
    create_engine = None
    text = None
    Engine = t.Any

try:
    from dotenv import load_dotenv

    _REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    load_dotenv(os.path.join(_REPO, ".env"), override=False)
except ImportError:
    pass


def _normalize_database_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if len(u) >= 2 and u[0] == u[-1] and u[0] in ("\"", "'"):
        u = u[1:-1].strip()
    if u.startswith("postgres://"):
        u = "postgresql://" + u[len("postgres://") :]

    if u.startswith("postgresql://"):
        u = "postgresql+psycopg://" + u[len("postgresql://") :]
    return u


_ENGINE: Engine | None = None


def database_url() -> str:
    return _normalize_database_url(
        os.getenv("DATABASE_URL") or os.getenv("PERSISTENCE_DSN") or ""
    )


def db_configured() -> bool:
    return bool(database_url())


def get_engine() -> Engine:
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE

    if create_engine is None:
        raise RuntimeError("sqlalchemy not installed")

    url = database_url()
    if not url:
        raise RuntimeError("DATABASE_URL not set")

    _ENGINE = create_engine(
        url,
        pool_pre_ping=True,
        pool_size=int(os.getenv("DB_POOL_SIZE") or 5),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW") or 10),
    )
    return _ENGINE


def db_ping() -> dict[str, t.Any]:
    if not db_configured():
        return {"ok": False, "configured": False}

    if text is None:
        return {"ok": False, "configured": True, "error": "sqlalchemy not installed"}

    try:
        eng = get_engine()
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"ok": True, "configured": True}
    except Exception as e:
        return {
            "ok": False,
            "configured": True,
            "error": f"{type(e).__name__}: {str(e)[:200]}",
        }


def get_session():
    from infrastructure.db_session import get_session as _get_session

    return _get_session()


__all__ = [
    "database_url",
    "db_configured",
    "db_ping",
    "get_engine",
    "get_session",
]

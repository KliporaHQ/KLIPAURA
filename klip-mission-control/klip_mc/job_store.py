"""
Async SQLAlchemy persistence for Mission Control jobs (``main.py``).

Uses ``DATABASE_URL`` — SQLite (default) or PostgreSQL (``postgresql+asyncpg://...``).
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text, JSON, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class JobRow(Base):
    __tablename__ = "mc_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    module: Mapped[str] = mapped_column(String(64))
    job_type: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))
    progress: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    result: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    hitl_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    hitl_approved: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


_engine = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def _normalize_database_url(url: str) -> str:
    u = url.strip()
    if u.startswith("postgresql+asyncpg://") or u.startswith("sqlite+aiosqlite://"):
        return u
    if u.startswith("postgresql://"):
        return u.replace("postgresql://", "postgresql+asyncpg://", 1)
    if u.startswith("postgres://"):
        return u.replace("postgres://", "postgresql+asyncpg://", 1)
    if u.startswith("sqlite:///"):
        return u.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    return u


async def init_job_store() -> bool:
    """Create engine and tables. Uses ``DATABASE_URL`` or defaults to local SQLite."""
    global _engine, _session_factory
    url = (os.getenv("DATABASE_URL") or "sqlite:///./klipaura.db").strip()
    async_url = _normalize_database_url(url)
    connect_args: dict = {}
    if async_url.startswith("postgresql"):
        # asyncpg: fail fast if Postgres is unreachable (avoid hung Mission Control startup)
        connect_args["timeout"] = int(os.getenv("DATABASE_CONNECT_TIMEOUT", "15"))
    _engine = create_async_engine(async_url, echo=False, connect_args=connect_args or {})
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return True


async def close_job_store() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


def job_store_enabled() -> bool:
    return _session_factory is not None


async def upsert_job_row(
    job_id: str,
    module: str,
    job_type: str,
    status: str,
    progress: int,
    priority: int,
    payload: Dict[str, Any],
    result: Optional[Dict[str, Any]],
    error: Optional[str],
    hitl_requested: bool,
    hitl_approved: Optional[bool],
    created_at: datetime,
    updated_at: datetime,
) -> None:
    if _session_factory is None:
        return
    async with _session_factory() as session:
        existing = await session.get(JobRow, job_id)
        if existing is None:
            row = JobRow(
                id=job_id,
                module=module,
                job_type=job_type,
                status=status,
                progress=progress,
                priority=priority,
                payload=payload,
                result=result,
                error=error,
                hitl_requested=hitl_requested,
                hitl_approved=hitl_approved,
                created_at=created_at,
                updated_at=updated_at,
            )
            session.add(row)
        else:
            existing.module = module
            existing.job_type = job_type
            existing.status = status
            existing.progress = progress
            existing.priority = priority
            existing.payload = payload
            existing.result = result
            existing.error = error
            existing.hitl_requested = hitl_requested
            existing.hitl_approved = hitl_approved
            existing.updated_at = updated_at
        await session.commit()


async def load_all_job_rows() -> List[JobRow]:
    if _session_factory is None:
        return []
    async with _session_factory() as session:
        result = await session.execute(select(JobRow).order_by(JobRow.created_at.desc()))
        return list(result.scalars().all())

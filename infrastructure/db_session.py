from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from infrastructure.db import get_engine

_SESSIONMAKER = None


def get_session() -> Session:
    global _SESSIONMAKER
    if _SESSIONMAKER is None:
        _SESSIONMAKER = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    return _SESSIONMAKER()

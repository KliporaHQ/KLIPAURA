from __future__ import annotations

from typing import Any

from sqlalchemy.dialects.postgresql import insert

from infrastructure.db_models import Opportunity
from infrastructure.db_session import get_session


def insert_opportunities(values: list[dict[str, Any]]) -> int:
    if not values:
        return 0

    inserted = 0
    with get_session() as session:
        stmt = insert(Opportunity).values(values)
        stmt = stmt.on_conflict_do_nothing(index_elements=["dedupe_hash"])
        res = session.execute(stmt)
        session.commit()
        try:
            inserted = int(res.rowcount or 0)
        except Exception:
            inserted = 0
    return inserted

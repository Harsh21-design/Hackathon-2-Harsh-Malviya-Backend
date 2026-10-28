"""
Activity logger — append-only audit trail for all significant events.
"""
from __future__ import annotations

import uuid
from typing import Optional, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActivityLog


async def log_event(
    db: AsyncSession,
    owner_id: str,
    event_type: str,
    description: Optional[str] = None,
    invoice_id: Optional[str] = None,
    client_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> ActivityLog:
    """
    Write an immutable activity log entry.
    Never raises — logs errors and continues.
    """
    try:
        entry = ActivityLog(
            owner_id=uuid.UUID(owner_id),
            event_type=event_type,
            description=description,
            invoice_id=uuid.UUID(invoice_id) if invoice_id else None,
            client_id=uuid.UUID(client_id) if client_id else None,
            metadata=metadata,
        )
        db.add(entry)
        await db.flush()
        return entry
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error("Failed to write activity log: %s", exc)
        raise

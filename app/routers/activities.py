"""
Activities router — Read-only audit log and payment verification history.
"""
from __future__ import annotations

import uuid
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import verify_jwt, get_current_user_id
from app.models import ActivityLog
from pydantic import BaseModel
from datetime import datetime

router = APIRouter(prefix="/api/v1/activities", tags=["Activities"])


class ActivityLogOut(BaseModel):
    id: str
    event_type: str
    description: Optional[str] = None
    invoice_id: Optional[str] = None
    client_id: Optional[str] = None
    created_at: datetime
    event_metadata: Optional[dict] = None

    class Config:
        from_attributes = True


@router.get("", response_model=list[ActivityLogOut])
async def list_activities(
    limit: int = Query(50, ge=1, le=100),
    invoice_id: Optional[str] = Query(None),
    payload: dict = Depends(verify_jwt),
    db: AsyncSession = Depends(get_db),
):
    user_id = get_current_user_id(payload)
    q = (
        select(ActivityLog)
        .where(ActivityLog.owner_id == uuid.UUID(user_id))
        .order_by(ActivityLog.created_at.desc())
        .limit(limit)
    )
    if invoice_id:
        try:
            q = q.where(ActivityLog.invoice_id == uuid.UUID(invoice_id))
        except ValueError:
            pass

    result = await db.execute(q)
    logs = result.scalars().all()

    return [
        ActivityLogOut(
            id=str(log.id),
            event_type=log.event_type,
            description=log.description,
            invoice_id=str(log.invoice_id) if log.invoice_id else None,
            client_id=str(log.client_id) if log.client_id else None,
            created_at=log.created_at,
            event_metadata=log.event_metadata,
        )
        for log in logs
    ]


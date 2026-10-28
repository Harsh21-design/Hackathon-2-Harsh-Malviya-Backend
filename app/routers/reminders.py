"""
Reminders router — the core demo feature.
Implements the two-phase draft/send pattern with double eligibility checking
and PostgreSQL advisory locks to prevent concurrent duplicate sends.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.security import verify_jwt, get_current_user_id
from app.models import (
    Invoice, Client, ReminderAttempt, PaymentPromise, PaymentReview,
    BusinessProfile, ActivityLog,
)
from app.schemas import (
    ReminderDraftRequest, ReminderDraftOut,
    ReminderSendRequest, ReminderAttemptOut,
)
from app.services.eligibility import check_eligibility
from app.services.ai_service import draft_reminder
from app.services.email_service import send_reminder_email
from app.services.activity_logger import log_event
from app.core.config import get_settings

router = APIRouter(prefix="/api/v1/invoices", tags=["Reminders"])
settings = get_settings()

# In-memory draft store (keyed by draft_id → draft dict).
# For production: use Redis. For MVP: sufficient — drafts are ephemeral.
_draft_cache: dict[str, dict] = {}


@router.post("/{invoice_id}/reminders/draft", response_model=ReminderDraftOut)
async def create_reminder_draft(
    invoice_id: str,
    req: ReminderDraftRequest,
    payload: dict = Depends(verify_jwt),
    db: AsyncSession = Depends(get_db),
):
    """
    Phase 1 of the two-phase send.
    Validates eligibility, generates an AI draft, returns it for user review.
    No email is sent and no permanent record is written here.
    """
    user_id = get_current_user_id(payload)
    inv, client, attempts, promises, reviews = await _load_invoice_context(invoice_id, user_id, db)

    eligibility = check_eligibility(inv, attempts, promises, reviews, requested_stage=req.stage)
    if not eligibility.eligible:
        raise HTTPException(status_code=400, detail=eligibility.reason)

    # Get business name for the draft
    profile_r = await db.execute(
        select(BusinessProfile).where(BusinessProfile.id == uuid.UUID(user_id))
    )
    profile = profile_r.scalar_one_or_none()
    business_name = profile.name if profile else "Your Business"

    # Prior successful sends for context
    sent = [a for a in attempts if a.status == "SENT"]

    draft = draft_reminder(inv, client, req.stage, business_name, sent)
    if not draft:
        raise HTTPException(500, "Failed to generate AI draft. Please try again.")

    # Persist a DRAFT_ONLY reminder attempt as the idempotency anchor
    draft_attempt = ReminderAttempt(
        invoice_id=inv.id,
        owner_id=uuid.UUID(user_id),
        stage=req.stage,
        status="DRAFT_ONLY",
        subject=draft["subject"],
        body=draft["body"],
    )
    db.add(draft_attempt)
    await db.commit()
    await db.refresh(draft_attempt)

    is_demo = settings.DEMO_MODE or not settings.RESEND_API_KEY

    # Cache the draft for the send step
    _draft_cache[str(draft_attempt.id)] = {
        "invoice_id": str(inv.id),
        "client_email": client.email,
        "client_name": client.name,
        "stage": req.stage,
        "subject": draft["subject"],
        "body": draft["body"],
        "user_id": user_id,
        "is_demo": is_demo,
    }

    return ReminderDraftOut(
        draft_id=str(draft_attempt.id),
        stage=req.stage,
        subject=draft["subject"],
        body=draft["body"],
        invoice_id=invoice_id,
        client_name=client.name,
        client_email=client.email,
        is_demo=is_demo,
    )


@router.post("/reminders/{draft_id}/send", response_model=ReminderAttemptOut)
async def send_reminder(
    draft_id: str,
    req: ReminderSendRequest,
    payload: dict = Depends(verify_jwt),
    db: AsyncSession = Depends(get_db),
):
    """
    Phase 2 of the two-phase send.
    Re-validates eligibility (with advisory lock), calls Resend, writes the record.
    Rule 16: Only writes SENT if Resend confirms delivery.
    Rule 12: Advisory lock + idempotency key prevent duplicates.
    """
    user_id = get_current_user_id(payload)

    # Retrieve draft
    cached = _draft_cache.get(draft_id)
    if not cached:
        # Also try to look up the DRAFT_ONLY attempt in DB (survives server restarts)
        try:
            did = uuid.UUID(draft_id)
        except ValueError:
            raise HTTPException(400, "Invalid draft ID.")

        attempt_r = await db.execute(
            select(ReminderAttempt).where(
                ReminderAttempt.id == did,
                ReminderAttempt.owner_id == uuid.UUID(user_id),
                ReminderAttempt.status == "DRAFT_ONLY",
            )
        )
        draft_attempt = attempt_r.scalar_one_or_none()
        if not draft_attempt:
            raise HTTPException(404, "Draft not found or already sent. Please generate a new draft.")

        invoice_id = str(draft_attempt.invoice_id)
    else:
        invoice_id = cached["invoice_id"]
        if cached["user_id"] != user_id:
            raise HTTPException(403, "Access denied.")

    inv, client, attempts, promises, reviews = await _load_invoice_context(invoice_id, user_id, db)

    # Re-validate eligibility (Rule 12 / Rule 18)
    stage = cached["stage"] if cached else None
    if stage is None:
        attempt_r2 = await db.execute(
            select(ReminderAttempt).where(ReminderAttempt.id == uuid.UUID(draft_id))
        )
        da = attempt_r2.scalar_one_or_none()
        stage = da.stage if da else None

    if stage is None:
        raise HTTPException(400, "Cannot determine reminder stage from draft.")

    # Acquire advisory lock to prevent concurrent sends (Rule 12)
    lock_key = abs(hash(invoice_id)) % (2**31)
    await db.execute(text(f"SELECT pg_advisory_xact_lock({lock_key})"))

    # Reload attempts after acquiring lock
    attempts_r = await db.execute(
        select(ReminderAttempt).where(ReminderAttempt.invoice_id == inv.id)
    )
    attempts = attempts_r.scalars().all()

    eligibility = check_eligibility(inv, attempts, promises, reviews, requested_stage=stage)
    if not eligibility.eligible:
        raise HTTPException(status_code=400, detail=eligibility.reason)

    is_demo = settings.DEMO_MODE or not settings.RESEND_API_KEY

    # Send email (Rule 16: only write SENT on confirmed delivery)
    send_result = send_reminder_email(
        to_email=client.email,
        subject=req.subject,
        body=req.body,
        invoice_id=invoice_id,
        demo_mode=is_demo,
    )

    # Update the DRAFT_ONLY attempt to reflect the result
    attempt_r3 = await db.execute(
        select(ReminderAttempt).where(
            ReminderAttempt.id == uuid.UUID(draft_id),
            ReminderAttempt.owner_id == uuid.UUID(user_id),
        )
    )
    reminder = attempt_r3.scalar_one_or_none()

    if send_result["success"]:
        if reminder:
            reminder.status = "SENT"
            reminder.resend_message_id = send_result["message_id"]
            reminder.subject = req.subject
            reminder.body = req.body
            reminder.sent_at = datetime.utcnow()
        else:
            reminder = ReminderAttempt(
                id=uuid.UUID(draft_id),
                invoice_id=inv.id,
                owner_id=uuid.UUID(user_id),
                stage=stage,
                status="SENT",
                resend_message_id=send_result["message_id"],
                subject=req.subject,
                body=req.body,
                sent_at=datetime.utcnow(),
            )
            db.add(reminder)

        # Update invoice
        inv.reminder_count += 1
        inv.status = f"REMINDER_{stage}_SENT"
        inv.last_reminder_at = datetime.utcnow()

        await db.flush()
        await log_event(
            db, user_id, "REMINDER_SENT",
            f"R{stage} reminder sent for invoice {inv.invoice_number}.",
            invoice_id=invoice_id,
            metadata={
                "stage": stage,
                "message_id": send_result["message_id"],
                "demo": send_result.get("demo", False),
            },
        )

    else:
        if reminder:
            reminder.status = "FAILED"
            reminder.error_message = send_result.get("error", "Unknown error")

        await db.flush()
        await log_event(
            db, user_id, "REMINDER_FAILED",
            f"R{stage} send failed for invoice {inv.invoice_number}: {send_result.get('error')}",
            invoice_id=invoice_id,
            metadata={"stage": stage, "error": send_result.get("error")},
        )

    await db.commit()

    # Clean up draft cache
    _draft_cache.pop(draft_id, None)

    if not send_result["success"]:
        raise HTTPException(
            status_code=502,
            detail=f"Email send failed: {send_result.get('error', 'Unknown error')}. "
                   "No reminder has been recorded.",
        )

    await db.refresh(reminder)
    return reminder


@router.get("/{invoice_id}/reminders", response_model=list[ReminderAttemptOut])
async def list_reminders(
    invoice_id: str,
    payload: dict = Depends(verify_jwt),
    db: AsyncSession = Depends(get_db),
):
    """Return all reminder attempts (SENT and FAILED) for an invoice."""
    user_id = get_current_user_id(payload)
    await _assert_invoice_ownership(invoice_id, user_id, db)

    result = await db.execute(
        select(ReminderAttempt)
        .where(
            ReminderAttempt.invoice_id == uuid.UUID(invoice_id),
            ReminderAttempt.owner_id == uuid.UUID(user_id),
            ReminderAttempt.status.in_(["SENT", "FAILED"]),
        )
        .order_by(ReminderAttempt.created_at)
    )
    return result.scalars().all()


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _load_invoice_context(invoice_id: str, user_id: str, db: AsyncSession):
    try:
        iid = uuid.UUID(invoice_id)
    except ValueError:
        raise HTTPException(400, "Invalid invoice ID.")

    inv_r = await db.execute(
        select(Invoice)
        .where(Invoice.id == iid, Invoice.owner_id == uuid.UUID(user_id))
        .options(selectinload(Invoice.client))
    )
    inv = inv_r.scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found.")

    client = inv.client
    if not client:
        raise HTTPException(500, "Invoice has no associated client.")

    attempts_r = await db.execute(select(ReminderAttempt).where(ReminderAttempt.invoice_id == iid))
    promises_r = await db.execute(select(PaymentPromise).where(PaymentPromise.invoice_id == iid))
    reviews_r = await db.execute(select(PaymentReview).where(PaymentReview.invoice_id == iid))

    return (
        inv,
        client,
        attempts_r.scalars().all(),
        promises_r.scalars().all(),
        reviews_r.scalars().all(),
    )


async def _assert_invoice_ownership(invoice_id: str, user_id: str, db: AsyncSession):
    try:
        iid = uuid.UUID(invoice_id)
    except ValueError:
        raise HTTPException(400, "Invalid invoice ID.")
    r = await db.execute(
        select(Invoice.id).where(
            Invoice.id == iid, Invoice.owner_id == uuid.UUID(user_id)
        )
    )
    if not r.scalar_one_or_none():
        raise HTTPException(404, "Invoice not found.")

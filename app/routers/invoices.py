"""
Invoices router — CRUD plus eligibility queries.
Status is computed on every read: due_date < today → OVERDUE (if still PENDING).
"""
from __future__ import annotations

from datetime import date
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.security import verify_jwt, get_current_user_id
from app.models import Client, Invoice, ReminderAttempt, PaymentPromise, PaymentReview
from app.schemas import (
    InvoiceCreate, InvoiceUpdate, InvoiceOut, InvoiceListOut,
    EligibilityOut, ClientOut,
)
from app.services.eligibility import check_eligibility
from app.services.activity_logger import log_event

router = APIRouter(prefix="/api/v1/invoices", tags=["Invoices"])


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("", response_model=InvoiceListOut)
async def list_invoices(
    status: Optional[str] = Query(None),
    client_id: Optional[str] = Query(None),
    payload: dict = Depends(verify_jwt),
    db: AsyncSession = Depends(get_db),
):
    user_id = get_current_user_id(payload)
    q = (
        select(Invoice)
        .where(Invoice.owner_id == uuid.UUID(user_id))
        .options(selectinload(Invoice.client))
        .order_by(Invoice.due_date)
    )

    if client_id:
        try:
            q = q.where(Invoice.client_id == uuid.UUID(client_id))
        except ValueError:
            raise HTTPException(400, "Invalid client_id format.")

    result = await db.execute(q)
    invoices = result.scalars().all()

    # Compute OVERDUE status live
    today = date.today()
    out = []
    for inv in invoices:
        inv_out = _build_invoice_out(inv, today)
        if status and inv_out.status != status:
            continue
        out.append(inv_out)

    return InvoiceListOut(items=out, total=len(out))


# ── Get one ───────────────────────────────────────────────────────────────────

@router.get("/{invoice_id}", response_model=InvoiceOut)
async def get_invoice(
    invoice_id: str,
    payload: dict = Depends(verify_jwt),
    db: AsyncSession = Depends(get_db),
):
    user_id = get_current_user_id(payload)
    inv = await _get_invoice_or_404(invoice_id, user_id, db, load_client=True)
    return _build_invoice_out(inv, date.today())


# ── Create ────────────────────────────────────────────────────────────────────

@router.post("", response_model=InvoiceOut, status_code=201)
async def create_invoice(
    data: InvoiceCreate,
    payload: dict = Depends(verify_jwt),
    db: AsyncSession = Depends(get_db),
):
    user_id = get_current_user_id(payload)

    # Resolve or create Client
    client_obj: Optional[Client] = None
    if data.client_id:
        client_result = await db.execute(
            select(Client).where(
                Client.id == uuid.UUID(str(data.client_id)),
                Client.owner_id == uuid.UUID(user_id),
            )
        )
        client_obj = client_result.scalar_one_or_none()
    elif data.client_email:
        # Match by email for this owner
        client_result = await db.execute(
            select(Client).where(
                Client.email == data.client_email.lower(),
                Client.owner_id == uuid.UUID(user_id),
            )
        )
        client_obj = client_result.scalar_one_or_none()
        if not client_obj:
            # Create new client automatically with provided name/email
            client_obj = Client(
                id=uuid.uuid4(),
                owner_id=uuid.UUID(user_id),
                name=data.client_name or data.client_email.split("@")[0].title(),
                email=data.client_email.lower(),
            )
            db.add(client_obj)
            await db.flush()

    if not client_obj:
        raise HTTPException(400, "Please provide client_id or client_name and client_email.")

    # Check duplicate invoice number for this owner
    dup = await db.execute(
        select(Invoice).where(
            Invoice.owner_id == uuid.UUID(user_id),
            Invoice.invoice_number == data.invoice_number,
        )
    )
    if dup.scalar_one_or_none():
        raise HTTPException(409, f"Invoice number '{data.invoice_number}' already exists.")

    inv = Invoice(
        id=uuid.uuid4(),
        owner_id=uuid.UUID(user_id),
        client_id=client_obj.id,
        invoice_number=data.invoice_number,
        amount=data.amount,
        currency=data.currency,
        invoice_date=data.invoice_date,
        due_date=data.due_date,
        description=data.description,
        status="PENDING",
    )
    db.add(inv)
    await db.flush()
    await log_event(
        db, user_id, "INVOICE_CREATED",
        f"Invoice {inv.invoice_number} created for {data.amount} {data.currency} ({client_obj.name} - {client_obj.email}).",
        invoice_id=str(inv.id), client_id=str(client_obj.id),
        metadata={"amount": str(data.amount), "due_date": str(data.due_date)},
    )
    await db.commit()
    await db.refresh(inv)

    result = await db.execute(
        select(Invoice).where(Invoice.id == inv.id).options(selectinload(Invoice.client))
    )
    inv = result.scalar_one()
    return _build_invoice_out(inv, date.today())


# ── Update ────────────────────────────────────────────────────────────────────

@router.patch("/{invoice_id}", response_model=InvoiceOut)
async def update_invoice(
    invoice_id: str,
    data: InvoiceUpdate,
    payload: dict = Depends(verify_jwt),
    db: AsyncSession = Depends(get_db),
):
    user_id = get_current_user_id(payload)
    inv = await _get_invoice_or_404(invoice_id, user_id, db)

    if inv.status == "PAID":
        raise HTTPException(400, "Cannot edit a paid invoice.")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(inv, field, value)

    await db.commit()
    await db.refresh(inv)

    result = await db.execute(
        select(Invoice).where(Invoice.id == inv.id).options(selectinload(Invoice.client))
    )
    inv = result.scalar_one()
    return _build_invoice_out(inv, date.today())


# ── Delete / Cancel ───────────────────────────────────────────────────────────

@router.delete("/{invoice_id}", status_code=204)
async def cancel_invoice(
    invoice_id: str,
    payload: dict = Depends(verify_jwt),
    db: AsyncSession = Depends(get_db),
):
    user_id = get_current_user_id(payload)
    inv = await _get_invoice_or_404(invoice_id, user_id, db)

    if inv.status == "PAID":
        raise HTTPException(400, "Cannot cancel a paid invoice.")

    inv.status = "CANCELLED"
    await db.commit()


# ── Eligibility ───────────────────────────────────────────────────────────────

@router.get("/{invoice_id}/eligibility", response_model=EligibilityOut)
async def get_eligibility(
    invoice_id: str,
    payload: dict = Depends(verify_jwt),
    db: AsyncSession = Depends(get_db),
):
    """Returns the current reminder eligibility status for a given invoice."""
    user_id = get_current_user_id(payload)
    inv = await _get_invoice_or_404(invoice_id, user_id, db)

    attempts_r = await db.execute(
        select(ReminderAttempt).where(ReminderAttempt.invoice_id == inv.id)
    )
    promises_r = await db.execute(
        select(PaymentPromise).where(PaymentPromise.invoice_id == inv.id)
    )
    reviews_r = await db.execute(
        select(PaymentReview).where(PaymentReview.invoice_id == inv.id)
    )

    result = check_eligibility(
        inv,
        attempts_r.scalars().all(),
        promises_r.scalars().all(),
        reviews_r.scalars().all(),
    )

    return EligibilityOut(
        eligible=result.eligible,
        next_stage=result.next_stage,
        reason=result.reason,
        reminder_count=result.reminder_count,
        has_active_promise=result.has_active_promise,
        has_open_verification=result.has_open_verification,
        has_open_dispute=result.has_open_dispute,
    )


# ── Verify Payment ────────────────────────────────────────────────────────────

@router.post("/{invoice_id}/verify-payment", status_code=200)
async def verify_payment(
    invoice_id: str,
    payload: dict = Depends(verify_jwt),
    db: AsyncSession = Depends(get_db),
):
    """
    Rule 11: Only a verified payment confirmed by the owner can mark an invoice as PAID.
    """
    user_id = get_current_user_id(payload)
    inv = await _get_invoice_or_404(invoice_id, user_id, db)

    if inv.status == "PAID":
        raise HTTPException(400, "Invoice is already marked as paid.")

    if inv.status == "CANCELLED":
        raise HTTPException(400, "Cannot verify payment on a cancelled invoice.")

    # Close any open verification reviews
    reviews_r = await db.execute(
        select(PaymentReview).where(
            PaymentReview.invoice_id == inv.id,
            PaymentReview.review_type == "VERIFICATION",
            PaymentReview.status == "OPEN",
        )
    )
    for review in reviews_r.scalars().all():
        from datetime import datetime
        review.status = "VERIFIED_PAID"
        review.resolved_at = datetime.utcnow()

    # Close any active promises
    promises_r = await db.execute(
        select(PaymentPromise).where(
            PaymentPromise.invoice_id == inv.id,
            PaymentPromise.status == "ACTIVE",
        )
    )
    for promise in promises_r.scalars().all():
        promise.status = "FULFILLED"

    inv.status = "PAID"
    await db.flush()
    await log_event(
        db, user_id, "PAYMENT_VERIFIED",
        f"Invoice {inv.invoice_number} marked as PAID by owner.",
        invoice_id=str(inv.id),
    )
    await db.commit()
    return {"ok": True, "message": "Invoice marked as paid."}


# ── Reject Claim ──────────────────────────────────────────────────────────────

@router.post("/{invoice_id}/reject-claim", status_code=200)
async def reject_claim(
    invoice_id: str,
    payload: dict = Depends(verify_jwt),
    db: AsyncSession = Depends(get_db),
):
    user_id = get_current_user_id(payload)
    inv = await _get_invoice_or_404(invoice_id, user_id, db)

    reviews_r = await db.execute(
        select(PaymentReview).where(
            PaymentReview.invoice_id == inv.id,
            PaymentReview.review_type == "VERIFICATION",
            PaymentReview.status == "OPEN",
        )
    )
    reviews = reviews_r.scalars().all()
    if not reviews:
        raise HTTPException(400, "No open payment claim to reject.")

    from datetime import datetime
    for review in reviews:
        review.status = "REJECTED"
        review.resolved_at = datetime.utcnow()

    # Revert invoice to appropriate reminder status
    if inv.reminder_count == 0:
        inv.status = "OVERDUE"
    else:
        inv.status = f"REMINDER_{inv.reminder_count}_SENT"

    await db.flush()
    await log_event(
        db, user_id, "CLAIM_REJECTED",
        f"Payment claim rejected for invoice {inv.invoice_number}.",
        invoice_id=str(inv.id),
    )
    await db.commit()
    return {"ok": True, "message": "Payment claim rejected. Invoice returned to previous status."}


# ── Resolve Dispute ───────────────────────────────────────────────────────────

@router.post("/{invoice_id}/resolve-dispute", status_code=200)
async def resolve_dispute(
    invoice_id: str,
    payload: dict = Depends(verify_jwt),
    db: AsyncSession = Depends(get_db),
):
    user_id = get_current_user_id(payload)
    inv = await _get_invoice_or_404(invoice_id, user_id, db)

    reviews_r = await db.execute(
        select(PaymentReview).where(
            PaymentReview.invoice_id == inv.id,
            PaymentReview.review_type == "DISPUTE",
            PaymentReview.status == "OPEN",
        )
    )
    reviews = reviews_r.scalars().all()
    if not reviews:
        raise HTTPException(400, "No open dispute to resolve.")

    from datetime import datetime
    for review in reviews:
        review.status = "RESOLVED"
        review.resolved_at = datetime.utcnow()

    if inv.reminder_count == 0:
        inv.status = "OVERDUE"
    else:
        inv.status = f"REMINDER_{inv.reminder_count}_SENT"

    await db.flush()
    await log_event(
        db, user_id, "DISPUTE_RESOLVED",
        f"Dispute resolved for invoice {inv.invoice_number}.",
        invoice_id=str(inv.id),
    )
    await db.commit()
    return {"ok": True, "message": "Dispute resolved. Invoice returned to previous status."}


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_invoice_or_404(
    invoice_id: str, user_id: str, db: AsyncSession, load_client: bool = False
) -> Invoice:
    try:
        iid = uuid.UUID(invoice_id)
    except ValueError:
        raise HTTPException(400, "Invalid invoice ID format.")

    q = select(Invoice).where(
        Invoice.id == iid,
        Invoice.owner_id == uuid.UUID(user_id),
    )
    if load_client:
        q = q.options(selectinload(Invoice.client))

    result = await db.execute(q)
    inv = result.scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found.")
    return inv


def _build_invoice_out(inv: Invoice, today: date) -> InvoiceOut:
    """Compute derived fields (overdue_days, live OVERDUE status)."""
    status = inv.status
    overdue_days = 0

    if inv.due_date < today and status == "PENDING":
        status = "OVERDUE"
        overdue_days = (today - inv.due_date).days
    elif inv.due_date < today and status not in ("PAID", "CANCELLED"):
        overdue_days = (today - inv.due_date).days

    client_out = ClientOut.model_validate(inv.client) if inv.client else None

    return InvoiceOut(
        id=str(inv.id),
        client_id=str(inv.client_id),
        invoice_number=inv.invoice_number,
        amount=inv.amount,
        currency=inv.currency,
        invoice_date=inv.invoice_date,
        due_date=inv.due_date,
        description=inv.description,
        status=status,
        reminder_count=inv.reminder_count,
        last_reminder_at=inv.last_reminder_at,
        overdue_days=overdue_days,
        created_at=inv.created_at,
        updated_at=inv.updated_at,
        client=client_out,
    )

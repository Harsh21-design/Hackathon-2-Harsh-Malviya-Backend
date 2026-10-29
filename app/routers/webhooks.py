"""
Webhooks router for Resend email events and inbound replies.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
import uuid
import re

from fastapi import APIRouter, Request, HTTPException, status, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.config import get_settings
from app.models import Invoice, Client, IncomingMessage, ActivityLog, new_uuid
from app.services.email_service import verify_webhook_signature
from app.services.ai_service import classify_reply

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/v1/webhooks", tags=["Webhooks"])


@router.post("/resend")
async def handle_resend_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Receives and processes Resend webhook events:
    - email.delivered, email.bounced, email.complained
    - email.received (client replies)
    """
    body_bytes = await request.body()
    signature = request.headers.get("svix-signature") or request.headers.get("resend-signature") or ""

    # Verify signature if secret configured
    if settings.RESEND_WEBHOOK_SECRET and signature:
        if not verify_webhook_signature(body_bytes, signature):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature",
            )

    try:
        data = await request.json()
    except Exception as e:
        logger.error("Failed to parse JSON body: %s", e)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON")

    event_type = data.get("type", "")
    payload = data.get("data", {})
    logger.info("Received Resend webhook event: %s", event_type)

    # ── Handle Inbound Client Reply (email.received) ─────────────────────────
    if event_type == "email.received":
        from_email = payload.get("from", "")
        # Extract plain email if format is 'Name <email@domain.com>'
        email_match = re.search(r"[\w\.-]+@[\w\.-]+", from_email)
        clean_email = email_match.group(0).lower() if email_match else from_email.lower()

        subject = payload.get("subject", "")
        body_text = payload.get("text", "") or payload.get("html", "")
        resend_message_id = payload.get("email_id") or payload.get("id")

        # Attempt to link to client & invoice
        client_res = await db.execute(select(Client).where(Client.email.ilike(clean_email)))
        client = client_res.scalar_one_or_none()

        matched_invoice = None
        owner_id = None

        if client:
            owner_id = client.owner_id
            # Find the most relevant open or pending invoice for this client
            inv_res = await db.execute(
                select(Invoice)
                .where(Invoice.client_id == client.id)
                .order_by(Invoice.due_date.asc())
            )
            matched_invoice = inv_res.scalars().first()

        if not owner_id and matched_invoice:
            owner_id = matched_invoice.owner_id
        elif not owner_id:
            # Fallback to demo/configured owner
            from app.core.security import DEMO_USER_ID
            owner_id = uuid.UUID(DEMO_USER_ID)

        # Store incoming message
        inc_msg = IncomingMessage(
            id=new_uuid(),
            owner_id=owner_id,
            invoice_id=matched_invoice.id if matched_invoice else None,
            client_id=client.id if client else None,
            from_email=clean_email,
            subject=subject,
            body_text=body_text,
            resend_message_id=resend_message_id,
            received_at=datetime.now(timezone.utc),
            processing_status="PROCESSED" if matched_invoice else "UNMATCHED",
        )
        db.add(inc_msg)

        # Log to activity feed
        client_display = client.name if client else clean_email
        inv_str = f" for invoice #{matched_invoice.invoice_number}" if matched_invoice else ""
        act = ActivityLog(
            id=new_uuid(),
            owner_id=owner_id,
            invoice_id=matched_invoice.id if matched_invoice else None,
            client_id=client.id if client else None,
            event_type="EMAIL_RECEIVED",
            description=f"Received client reply from {client_display}{inv_str}: '{subject}'",
            event_metadata={"from": clean_email, "preview": body_text[:120]},
        )
        db.add(act)

        # Classify with AI if body exists
        if body_text and matched_invoice:
            try:
                classification = classify_reply(
                    reply_text=body_text,
                    invoice_amount=float(matched_invoice.amount),
                    due_date=matched_invoice.due_date.isoformat(),
                )
                logger.info("AI classification result: %s", classification.get("classification"))
            except Exception as ce:
                logger.warning("AI classification failed: %s", ce)

        await db.commit()
        return {"status": "success", "action": "processed_reply"}

    # ── Handle Delivery / Status Events ──────────────────────────────────────
    elif event_type in ("email.delivered", "email.bounced", "email.complained"):
        recipient = payload.get("to", [""])[0] if isinstance(payload.get("to"), list) else payload.get("to", "")
        logger.info("Email event %s for %s", event_type, recipient)

        # Log delivery activity
        from app.core.security import DEMO_USER_ID
        act = ActivityLog(
            id=new_uuid(),
            owner_id=uuid.UUID(DEMO_USER_ID),
            event_type=event_type.upper().replace(".", "_"),
            description=f"Email delivery status updated to {event_type} for {recipient}",
            event_metadata=payload,
        )
        db.add(act)
        await db.commit()
        return {"status": "success", "event": event_type}

    return {"status": "ignored", "event_type": event_type}


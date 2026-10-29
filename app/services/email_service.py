"""
Email service — Resend integration for sending and receiving emails.
"""
from __future__ import annotations

import logging
from typing import Optional
import resend

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _resend_configured() -> bool:
    return bool(settings.RESEND_API_KEY and settings.RESEND_API_KEY != "")


def send_reminder_email(
    to_email: str,
    subject: str,
    body: str,
    invoice_id: str,
    demo_mode: bool = False,
) -> dict:
    """
    Send a reminder email via Resend.
    Returns dict with: success, message_id, error
    Rule 16: Only returns success=True if Resend confirms delivery.
    """
    if demo_mode or not _resend_configured():
        logger.info("[DEMO] Simulated email to %s: %s", to_email, subject)
        return {
            "success": True,
            "message_id": f"demo_{invoice_id}_{subject[:10].replace(' ', '_')}",
            "demo": True,
        }

    try:
        resend.api_key = settings.RESEND_API_KEY

        reply_to = (
            f"reply+{invoice_id}@{settings.RESEND_REPLY_TO_DOMAIN}"
            if settings.RESEND_REPLY_TO_DOMAIN
            else settings.RESEND_FROM_EMAIL
        )

        # Convert plain text body to simple HTML
        html_body = body.replace("\n", "<br>")

        from_email = settings.RESEND_FROM_EMAIL or "onboarding@resend.dev"

        params = {
            "from": from_email,
            "to": [to_email],
            "subject": subject,
            "html": f"<div style='font-family: Arial, sans-serif; max-width: 600px;'>{html_body}</div>",
            "reply_to": reply_to,
        }

        response = resend.Emails.send(params)
        message_id = response.get("id") if isinstance(response, dict) else (getattr(response, "id", None) or str(response))

        return {"success": True, "message_id": message_id, "demo": False}

    except Exception as e:
        logger.error("Resend send error: %s", e)
        return {"success": False, "message_id": None, "error": str(e), "demo": False}


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """
    Verify Resend webhook signature using HMAC-SHA256.
    Returns True if valid or if webhook secret is not configured (dev mode).
    """
    if not settings.RESEND_WEBHOOK_SECRET:
        logger.warning("RESEND_WEBHOOK_SECRET not set — skipping signature verification.")
        return True

    import hmac
    import hashlib

    expected = hmac.new(
        settings.RESEND_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature.removeprefix("sha256="))

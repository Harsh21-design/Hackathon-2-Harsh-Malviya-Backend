"""
AI service — Gemini integration for:
  1. Reply classification (structured JSON output via Pydantic schema)
  2. Reminder email draft generation
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Optional

import google.generativeai as genai
from pydantic import ValidationError

from app.core.config import get_settings
from app.schemas import AIClassificationResult
from app.models import Invoice, Client, ReminderAttempt

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Gemini client (lazy init) ─────────────────────────────────────────────────

_client: Optional[genai.GenerativeModel] = None


def _get_model() -> genai.GenerativeModel:
    global _client
    if _client is None:
        api_key = settings.GEMINI_API_KEY
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not configured.")
        genai.configure(api_key=api_key)
        _client = genai.GenerativeModel(settings.GEMINI_MODEL)
    return _client


# ── Reply Classification ──────────────────────────────────────────────────────

CLASSIFICATION_SYSTEM = """You are an AI assistant for a payment recovery application.
Your task is to classify an incoming client email reply about an overdue invoice.

Classification labels:
- PAYMENT_PROMISE: The client clearly states they will pay on a specific date, within a specific number of days, or by a specific day of the week. Do NOT use this if the client is vague (e.g., "soon", "shortly", "ASAP").
- PAYMENT_CLAIM: The client states they have ALREADY sent or made the payment.
- DISPUTE: The client disputes the invoice amount, the work done, or refuses to pay.
- OTHER_OR_PENDING: Anything that doesn't clearly fit the above categories.

Critical rules:
1. Only extract a promised_date if the client states a SPECIFIC date, a calculable number of days (e.g., "in 5 days"), or a specific day (e.g., "by Friday"). If the client says "soon", "shortly", "as soon as possible", or similar vague phrases, set promised_date to null.
2. Do not invent information not present in the client's message.
3. Your summary must be one factual sentence based only on the message content.
4. Your suggested_action must be practical and specific.
5. Set confidence to LOW if you are uncertain, MEDIUM if reasonably sure, HIGH if the message is unambiguous.

Respond with a JSON object matching exactly this schema:
{
  "label": "PAYMENT_PROMISE|PAYMENT_CLAIM|DISPUTE|OTHER_OR_PENDING",
  "promised_date": "YYYY-MM-DD or null",
  "summary": "one sentence summary",
  "suggested_action": "recommended next action for the business owner",
  "confidence": "HIGH|MEDIUM|LOW",
  "reasoning": "brief explanation of the classification"
}"""


def classify_reply(
    client_message: str,
    invoice: Invoice,
    client: Client,
) -> Optional[AIClassificationResult]:
    """
    Classify a client's reply email using Gemini structured output.
    Returns None if classification fails (caller should route to review queue).
    """
    if not settings.GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY not set; skipping AI classification.")
        return None

    today = date.today()
    overdue_days = (today - invoice.due_date).days if invoice.due_date < today else 0

    user_prompt = f"""Invoice context:
- Client: {client.name}
- Invoice number: {invoice.invoice_number}
- Amount: {invoice.currency} {invoice.amount}
- Due date: {invoice.due_date}
- Days overdue: {overdue_days}
- Today's date: {today}

Client's reply message:
\"\"\"
{client_message}
\"\"\"

Classify this reply and respond with the JSON schema described in the system prompt."""

    try:
        model = _get_model()
        response = model.generate_content(
            contents=[
                {"role": "user", "parts": [CLASSIFICATION_SYSTEM + "\n\n" + user_prompt]}
            ],
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )

        raw_text = response.text.strip()

        # Strip markdown code fences if present
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
            raw_text = raw_text.strip()

        result = AIClassificationResult.model_validate_json(raw_text)

        # Post-validation: promised_date sanity checks
        if result.label == "PAYMENT_PROMISE" and result.promised_date is None:
            logger.info("PAYMENT_PROMISE with null date — downgrading to OTHER_OR_PENDING")
            result.label = "OTHER_OR_PENDING"
            result.confidence = "LOW"
            result.reasoning += " (Downgraded: promised_date was null)"

        if result.promised_date and result.promised_date < today:
            logger.warning("promised_date %s is in the past — routing to review", result.promised_date)
            result.confidence = "LOW"
            result.reasoning += " (Warning: promised date is in the past)"

        return result

    except ValidationError as e:
        logger.error("Pydantic validation failed for AI classification response: %s", e)
        return None
    except Exception as e:
        logger.error("Gemini classification error: %s", e)
        return None


# ── Reminder Drafting ─────────────────────────────────────────────────────────

REMINDER_SYSTEM = """You are a professional payment recovery assistant for a freelancer/business owner.
Your task is to draft a CRISP, CONCISE, and POLITE payment reminder email.

CRITICAL INSTRUCTIONS:
1. KEEP IT VERY BRIEF: Exactly 2 to 3 short paragraphs maximum (under 90 words total). NEVER write a long essay or lengthy email.
2. Directly acknowledge any prior client response/note concisely if provided.
3. Include the invoice number, amount, and due date clearly.
4. Tone must be professional, warm, and collaborative.

Respond ONLY with a valid JSON object:
{
  "subject": "Clear concise subject line",
  "body": "Concise plain text email body with \\n for line breaks"
}"""

STAGE_INSTRUCTIONS = {
    1: "Stage 1 (Gentle): Short and polite reminder that the invoice reached its due date. Assume it might have been overlooked.",
    2: "Stage 2 (Firm Follow-up): Short follow-up acknowledging the previous note/delay. Request confirmation of the payment status.",
    3: "Stage 3 (Final Notice): Short, firm final follow-up requesting urgent payment or a direct update today.",
}


def draft_reminder(
    invoice: Invoice,
    client: Client,
    stage: int,
    business_name: str,
    prior_reminders: list[ReminderAttempt],
    prior_communication_summary: Optional[str] = None,
) -> dict[str, str]:
    """
    Generate a concise AI reminder email draft using direct Google REST API
    (fast, reliable, and immune to SDK gRPC hanging).
    """
    if not settings.GEMINI_API_KEY:
        return _fallback_draft(invoice, client, stage, business_name)

    today = date.today()
    overdue_days = (today - invoice.due_date).days if invoice.due_date < today else 0

    comm_note = ""
    if prior_communication_summary:
        comm_note = f"\nClient's recent note: {prior_communication_summary}"

    user_prompt = f"""Sender: {business_name}
Client: {client.name} ({client.email})
Invoice: #{invoice.invoice_number}
Amount: {invoice.currency} {invoice.amount}
Due Date: {invoice.due_date} ({overdue_days} days overdue)
{comm_note}

Stage: {STAGE_INSTRUCTIONS.get(stage, STAGE_INSTRUCTIONS[1])}

Draft a very short (2-3 sentences max) email draft now."""

    try:
        import requests
        model_name = settings.GEMINI_MODEL or "gemini-3.5-flash-lite"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={settings.GEMINI_API_KEY}"
        
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": f"{REMINDER_SYSTEM}\n\n{user_prompt}"}
                    ]
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.3,
                "maxOutputTokens": 300,
            }
        }
        
        resp = requests.post(url, json=payload, timeout=6)
        if resp.status_code == 200:
            data_json = resp.json()
            raw_text = data_json["candidates"][0]["content"]["parts"][0]["text"].strip()
            if raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]
                raw_text = raw_text.strip()
            
            parsed = json.loads(raw_text)
            if "subject" in parsed and "body" in parsed:
                return {"subject": parsed["subject"], "body": parsed["body"]}

        logger.warning("Gemini REST API non-200: %s %s", resp.status_code, resp.text)
        return _fallback_draft(invoice, client, stage, business_name)

    except Exception as e:
        logger.error("Gemini draft exception: %s", e)
        return _fallback_draft(invoice, client, stage, business_name)


def _fallback_draft(
    invoice: Invoice, client: Client, stage: int, business_name: str
) -> dict[str, str]:
    """
    Hardcoded fallback draft used when Gemini is unavailable.
    Safe, professional, no invented facts.
    """
    today = date.today()
    overdue_days = (today - invoice.due_date).days if invoice.due_date < today else 0

    stage_openers = {
        1: f"I hope this message finds you well. I wanted to reach out regarding invoice {invoice.invoice_number} for {invoice.currency} {invoice.amount}, which was due on {invoice.due_date}.",
        2: f"I'm following up on my previous message regarding invoice {invoice.invoice_number} for {invoice.currency} {invoice.amount}. As of today, the payment is {overdue_days} days overdue.",
        3: f"This is a final notice regarding invoice {invoice.invoice_number} for {invoice.currency} {invoice.amount}, which remains unpaid and is now {overdue_days} days past due.",
    }

    subject_stages = {
        1: f"Payment Reminder: Invoice {invoice.invoice_number}",
        2: f"Second Reminder: Invoice {invoice.invoice_number} — Payment Overdue",
        3: f"Final Notice: Invoice {invoice.invoice_number} — Immediate Attention Required",
    }

    opener = stage_openers.get(stage, stage_openers[1])
    subject = subject_stages.get(stage, subject_stages[1])

    body = f"""Dear {client.name},

{opener}

Could you please confirm the payment status or let me know if there is anything preventing the payment from being processed? I am happy to assist resolve any questions about this invoice.

Please don't hesitate to reach out if you need any clarification.

Best regards,
{business_name}

---
Invoice Number: {invoice.invoice_number}
Amount Due: {invoice.currency} {invoice.amount}
Due Date: {invoice.due_date}
"""
    return {"subject": subject, "body": body}

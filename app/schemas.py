"""
Pydantic request/response schemas.
All UUIDs are serialised as strings for JSON compatibility.
"""
from __future__ import annotations

import uuid
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, Literal, Any

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator, model_validator, field_serializer


# ── Shared ────────────────────────────────────────────────────────────────────

class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, coerce_numbers_to_str=True)

    @field_serializer("id", "client_id", "invoice_id", "incoming_message_id", "owner_id", "source_message_id", check_fields=False)
    def serialize_uuid(self, v: Any) -> Optional[str]:
        return str(v) if v is not None else None


class OkResponse(BaseSchema):
    ok: bool = True
    message: str = "Success"


# ── Business Profile ──────────────────────────────────────────────────────────

class BusinessProfileCreate(BaseModel):
    name: str
    email: EmailStr
    company: Optional[str] = None
    address: Optional[str] = None


class BusinessProfileUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    company: Optional[str] = None
    address: Optional[str] = None


class BusinessProfileOut(BaseModel):
    id: uuid.UUID | str
    name: str
    email: str
    company: Optional[str]
    address: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Client ────────────────────────────────────────────────────────────────────

class ClientCreate(BaseModel):
    name: str
    email: EmailStr
    company: Optional[str] = None
    phone: Optional[str] = None
    notes: Optional[str] = None


class ClientUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    company: Optional[str] = None
    phone: Optional[str] = None
    notes: Optional[str] = None


class ClientOut(BaseModel):
    id: uuid.UUID | str
    name: str
    email: str
    company: Optional[str]
    phone: Optional[str]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ClientWithStats(ClientOut):
    invoice_count: int = 0
    total_invoiced: Decimal = Decimal("0")
    outstanding_amount: Decimal = Decimal("0")


# ── Invoice ───────────────────────────────────────────────────────────────────

INVOICE_STATUSES = Literal[
    "PENDING", "OVERDUE",
    "REMINDER_1_SENT", "REMINDER_2_SENT", "REMINDER_3_SENT",
    "PAYMENT_PROMISED", "PAYMENT_CLAIMED", "DISPUTED",
    "PAID", "CANCELLED"
]


class InvoiceCreate(BaseModel):
    client_id: uuid.UUID | str
    invoice_number: str
    amount: Decimal
    currency: str = "USD"
    invoice_date: date
    due_date: date
    description: Optional[str] = None

    @field_validator("amount")
    @classmethod
    def amount_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Amount must be positive.")
        return v

    @model_validator(mode="after")
    def due_after_invoice(self) -> "InvoiceCreate":
        if self.due_date < self.invoice_date:
            raise ValueError("Due date must be on or after invoice date.")
        return self


class InvoiceUpdate(BaseModel):
    invoice_number: Optional[str] = None
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    invoice_date: Optional[date] = None
    due_date: Optional[date] = None
    description: Optional[str] = None


class InvoiceOut(BaseModel):
    id: uuid.UUID | str
    client_id: uuid.UUID | str
    invoice_number: str
    amount: Decimal
    currency: str
    invoice_date: date
    due_date: date
    description: Optional[str]
    status: str
    reminder_count: int
    last_reminder_at: Optional[datetime]
    overdue_days: int = 0
    created_at: datetime
    updated_at: datetime

    # Nested
    client: Optional[ClientOut] = None

    model_config = {"from_attributes": True}


class InvoiceListOut(BaseModel):
    items: list[InvoiceOut]
    total: int


# ── Reminder Attempt ──────────────────────────────────────────────────────────

class ReminderDraftRequest(BaseModel):
    stage: Literal[1, 2, 3]


class ReminderDraftOut(BaseModel):
    draft_id: uuid.UUID | str
    stage: int
    subject: str
    body: str
    invoice_id: uuid.UUID | str
    client_name: str
    client_email: str
    is_demo: bool = False


class ReminderSendRequest(BaseModel):
    draft_id: uuid.UUID | str
    subject: str   # user may have edited it
    body: str      # user may have edited it


class ReminderAttemptOut(BaseModel):
    id: uuid.UUID | str
    invoice_id: uuid.UUID | str
    stage: int
    status: str
    resend_message_id: Optional[str]
    subject: Optional[str]
    sent_at: Optional[datetime]
    error_message: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Eligibility ───────────────────────────────────────────────────────────────

class EligibilityOut(BaseModel):
    eligible: bool
    next_stage: Optional[int]
    reason: Optional[str]
    reminder_count: int
    has_active_promise: bool
    has_open_verification: bool
    has_open_dispute: bool


# ── Incoming Messages ─────────────────────────────────────────────────────────

class SimulateReplyRequest(BaseModel):
    """For demo mode: manually submit a client reply text for classification."""
    invoice_id: uuid.UUID | str
    from_email: str
    subject: Optional[str] = None
    body_text: str


class IncomingMessageOut(BaseModel):
    id: uuid.UUID | str
    invoice_id: Optional[str]
    client_id: Optional[str]
    from_email: str
    subject: Optional[str]
    body_text: Optional[str]
    received_at: datetime
    processing_status: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Reply Classification ──────────────────────────────────────────────────────

CLASSIFICATION_LABELS = Literal[
    "PAYMENT_PROMISE", "PAYMENT_CLAIM", "DISPUTE", "OTHER_OR_PENDING"
]


class AIClassificationResult(BaseModel):
    """Schema for structured output from Gemini."""
    label: CLASSIFICATION_LABELS
    promised_date: Optional[date] = None
    summary: str
    suggested_action: str
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    reasoning: str


class ReplyClassificationOut(BaseModel):
    id: uuid.UUID | str
    incoming_message_id: uuid.UUID | str
    invoice_id: Optional[str]
    label: str
    promised_date: Optional[date]
    summary: Optional[str]
    suggested_action: Optional[str]
    confidence: Optional[str]
    review_status: str
    human_label: Optional[str]
    reviewed_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class ReviewQueueItem(BaseModel):
    message: IncomingMessageOut
    classification: Optional[ReplyClassificationOut]
    invoice: Optional[InvoiceOut]


class ReviewApproveRequest(BaseModel):
    pass  # No body needed for approve


class ReviewOverrideRequest(BaseModel):
    label: CLASSIFICATION_LABELS
    notes: Optional[str] = None


# ── Payment Promise ───────────────────────────────────────────────────────────

class PaymentPromiseCreate(BaseModel):
    promised_date: date
    source_message_id: Optional[str] = None

    @field_validator("promised_date")
    @classmethod
    def date_not_past(cls, v: date) -> date:
        if v < date.today():
            raise ValueError("Promised date cannot be in the past.")
        return v


class PaymentPromiseOut(BaseModel):
    id: uuid.UUID | str
    invoice_id: uuid.UUID | str
    promised_date: date
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Payment Review ────────────────────────────────────────────────────────────

class PaymentReviewOut(BaseModel):
    id: uuid.UUID | str
    invoice_id: uuid.UUID | str
    review_type: str
    status: str
    notes: Optional[str]
    resolved_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class ResolveReviewRequest(BaseModel):
    notes: Optional[str] = None


# ── Activity Log ──────────────────────────────────────────────────────────────

class ActivityLogOut(BaseModel):
    id: uuid.UUID | str
    invoice_id: Optional[str]
    client_id: Optional[str]
    event_type: str
    description: Optional[str]
    metadata: Optional[Any]
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Dashboard ─────────────────────────────────────────────────────────────────

class ActionItem(BaseModel):
    type: str   # SEND_REMINDER | REVIEW_PROMISE | REVIEW_DISPUTE | REVIEW_REPLY | REVIEW_CLAIM
    invoice_id: uuid.UUID | str
    invoice_number: str
    client_name: str
    amount: Decimal
    overdue_days: int
    message: str
    next_stage: Optional[int] = None


class DashboardOut(BaseModel):
    total_outstanding: Decimal
    overdue_amount: Decimal
    active_promises_count: int
    invoices_needing_action: int
    action_items: list[ActionItem]
    recent_activity: list[ActivityLogOut]


# ── Settings ──────────────────────────────────────────────────────────────────

class AppSettingsUpdate(BaseModel):
    gemini_api_key: Optional[str] = None
    resend_api_key: Optional[str] = None
    resend_from_email: Optional[str] = None
    resend_reply_to_domain: Optional[str] = None
    demo_mode: Optional[bool] = None


class AppSettingsOut(BaseModel):
    demo_mode: bool
    resend_configured: bool
    gemini_configured: bool
    resend_from_email: str
    resend_reply_to_domain: str
    webhook_url: str

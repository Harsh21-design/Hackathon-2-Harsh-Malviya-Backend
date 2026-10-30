import uuid
from datetime import datetime, date
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    String, Numeric, Integer, Boolean, Text, Date,
    DateTime, ForeignKey, UniqueConstraint, CheckConstraint,
    func, Uuid, JSON,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB as PG_JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

# Cross-dialect type helpers
UUID_TYPE = Uuid(as_uuid=True)
JSON_TYPE = JSON().with_variant(PG_JSONB(), "postgresql")

from app.core.database import Base


# ── helpers ──────────────────────────────────────────────────────────────────

def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def now_utc() -> datetime:
    return datetime.utcnow()


# ── Models ───────────────────────────────────────────────────────────────────

class BusinessProfile(Base):
    __tablename__ = "business_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    company: Mapped[Optional[str]] = mapped_column(String(255))
    address: Mapped[Optional[str]] = mapped_column(Text)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=new_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    company: Mapped[Optional[str]] = mapped_column(String(255))
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    invoices: Mapped[list["Invoice"]] = relationship("Invoice", back_populates="client")


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("owner_id", "invoice_number", name="uq_owner_invoice_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=new_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, nullable=False, index=True)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False)
    invoice_number: Mapped[str] = mapped_column(String(100), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    # PENDING | OVERDUE | REMINDER_1_SENT | REMINDER_2_SENT | REMINDER_3_SENT
    # | PAYMENT_PROMISED | PAYMENT_CLAIMED | DISPUTED | PAID | CANCELLED
    status: Mapped[str] = mapped_column(String(30), default="PENDING", nullable=False, index=True)
    reminder_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_reminder_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    client: Mapped["Client"] = relationship("Client", back_populates="invoices")
    reminder_attempts: Mapped[list["ReminderAttempt"]] = relationship("ReminderAttempt", back_populates="invoice")
    incoming_messages: Mapped[list["IncomingMessage"]] = relationship("IncomingMessage", back_populates="invoice")
    payment_promises: Mapped[list["PaymentPromise"]] = relationship("PaymentPromise", back_populates="invoice")
    payment_reviews: Mapped[list["PaymentReview"]] = relationship("PaymentReview", back_populates="invoice")
    activity_logs: Mapped[list["ActivityLog"]] = relationship("ActivityLog", back_populates="invoice")


class ReminderAttempt(Base):
    __tablename__ = "reminder_attempts"
    __table_args__ = (
        CheckConstraint("stage IN (1, 2, 3)", name="chk_stage_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=new_uuid)
    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, nullable=False)
    stage: Mapped[int] = mapped_column(Integer, nullable=False)
    # SENT | FAILED | DRAFT_ONLY
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    resend_message_id: Mapped[Optional[str]] = mapped_column(String(255))
    subject: Mapped[Optional[str]] = mapped_column(Text)
    body: Mapped[Optional[str]] = mapped_column(Text)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="reminder_attempts")


class IncomingMessage(Base):
    __tablename__ = "incoming_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=new_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, nullable=False)
    invoice_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID_TYPE, ForeignKey("invoices.id"), index=True)
    client_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID_TYPE, ForeignKey("clients.id"))
    from_email: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[Optional[str]] = mapped_column(Text)
    body_text: Mapped[Optional[str]] = mapped_column(Text)
    resend_message_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True)
    in_reply_to: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # PENDING | PROCESSED | REVIEW_REQUIRED | DUPLICATE | UNMATCHED
    processing_status: Mapped[str] = mapped_column(String(30), default="PENDING", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    invoice: Mapped[Optional["Invoice"]] = relationship("Invoice", back_populates="incoming_messages")
    classification: Mapped[Optional["ReplyClassification"]] = relationship("ReplyClassification", back_populates="incoming_message", uselist=False)


class ReplyClassification(Base):
    __tablename__ = "reply_classifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=new_uuid)
    incoming_message_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("incoming_messages.id", ondelete="CASCADE"), nullable=False, unique=True)
    invoice_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID_TYPE, ForeignKey("invoices.id"))
    # PAYMENT_PROMISE | PAYMENT_CLAIM | DISPUTE | OTHER_OR_PENDING
    label: Mapped[str] = mapped_column(String(30), nullable=False)
    promised_date: Mapped[Optional[date]] = mapped_column(Date)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    suggested_action: Mapped[Optional[str]] = mapped_column(Text)
    raw_ai_response: Mapped[Optional[dict]] = mapped_column(JSON_TYPE)
    # HIGH | MEDIUM | LOW
    confidence: Mapped[Optional[str]] = mapped_column(String(10))
    # PENDING | APPROVED | OVERRIDDEN
    review_status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False)
    human_label: Mapped[Optional[str]] = mapped_column(String(30))
    reviewed_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID_TYPE)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    incoming_message: Mapped["IncomingMessage"] = relationship("IncomingMessage", back_populates="classification")


class PaymentPromise(Base):
    __tablename__ = "payment_promises"

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=new_uuid)
    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, nullable=False)
    promised_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_message_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID_TYPE, ForeignKey("incoming_messages.id"))
    # ACTIVE | EXPIRED | FULFILLED | CANCELLED
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="payment_promises")


class PaymentReview(Base):
    __tablename__ = "payment_reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=new_uuid)
    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, nullable=False)
    # VERIFICATION | DISPUTE
    review_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # OPEN | VERIFIED_PAID | REJECTED | RESOLVED
    status: Mapped[str] = mapped_column(String(20), default="OPEN", nullable=False)
    source_message_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID_TYPE, ForeignKey("incoming_messages.id"))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="payment_reviews")


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=new_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, nullable=False, index=True)
    invoice_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID_TYPE, ForeignKey("invoices.id"), index=True)
    client_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID_TYPE, ForeignKey("clients.id"))
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    event_metadata: Mapped[Optional[dict]] = mapped_column("metadata", JSON_TYPE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

    invoice: Mapped[Optional["Invoice"]] = relationship("Invoice", back_populates="activity_logs")

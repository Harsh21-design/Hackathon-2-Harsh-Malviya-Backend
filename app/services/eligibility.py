"""
Reminder eligibility engine.
This is the single authoritative source for all 18 business rules.
Called twice per reminder send: once for draft generation, once before
the actual Resend API call (with a DB advisory lock held).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from app.models import Invoice, ReminderAttempt, PaymentPromise, PaymentReview


@dataclass
class EligibilityResult:
    eligible: bool
    next_stage: Optional[int]
    reason: Optional[str]
    reminder_count: int
    has_active_promise: bool
    has_open_verification: bool
    has_open_dispute: bool


def check_eligibility(
    invoice: Invoice,
    attempts: list[ReminderAttempt],
    promises: list[PaymentPromise],
    reviews: list[PaymentReview],
    requested_stage: Optional[int] = None,
) -> EligibilityResult:
    """
    Evaluate all 18 business rules and return an EligibilityResult.

    Rules enforced here:
      Rule 1  — Max 3 successful reminders
      Rule 2  — Manual initiation only (enforced by router, not here)
      Rule 3  — R1 → R2 → R3 order
      Rule 4  — No reminders before due date
      Rule 5  — No reminders on paid invoices
      Rule 6  — Active promise pauses reminders
      Rule 7  — Open payment claim pauses reminders
      Rule 8  — Disputed invoices held
      Rule 12 — Duplicate prevention (stage already sent)
      Rule 16 — Failed sends don't count
    """
    today = date.today()

    # Compute auxiliary state
    sent_attempts = [a for a in attempts if a.status == "SENT"]
    reminder_count = len(sent_attempts)

    active_promise = _active_promise(promises, today)
    has_active_promise = active_promise is not None

    open_verification = _open_review(reviews, "VERIFICATION")
    has_open_verification = open_verification is not None

    open_dispute = _open_review(reviews, "DISPUTE")
    has_open_dispute = open_dispute is not None

    next_stage = reminder_count + 1 if reminder_count < 3 else None

    def ineligible(reason: str) -> EligibilityResult:
        return EligibilityResult(
            eligible=False,
            next_stage=next_stage,
            reason=reason,
            reminder_count=reminder_count,
            has_active_promise=has_active_promise,
            has_open_verification=has_open_verification,
            has_open_dispute=has_open_dispute,
        )

    def eligible() -> EligibilityResult:
        return EligibilityResult(
            eligible=True,
            next_stage=next_stage,
            reason=None,
            reminder_count=reminder_count,
            has_active_promise=has_active_promise,
            has_open_verification=has_open_verification,
            has_open_dispute=has_open_dispute,
        )

    # Rule 4: Not yet due
    if invoice.due_date > today:
        return ineligible(
            f"This invoice is not yet due (due {invoice.due_date}). "
            "Reminders can only be sent after the due date has passed."
        )

    # Rule 5: Already paid
    if invoice.status == "PAID":
        return ineligible("This invoice has already been marked as paid.")

    # Rule 8: Open dispute
    if has_open_dispute:
        return ineligible(
            "This invoice is under dispute review. Reminders are paused until the dispute is resolved."
        )

    # Rule 7: Open payment claim awaiting verification
    if has_open_verification:
        return ineligible(
            "A payment claim from the client is awaiting your verification. "
            "Reminders are paused until you verify or reject the claim."
        )

    # Rule 6: Active payment promise
    if has_active_promise:
        return ineligible(
            f"The client has promised to pay by {active_promise.promised_date}. "
            "Reminders are paused while this promise is active."
        )

    # Rule 1: Maximum 3 reminders
    if reminder_count >= 3:
        return ineligible(
            "The maximum of 3 reminders has already been sent for this invoice."
        )

    # Rule 3: Stage order
    if requested_stage is not None and requested_stage != next_stage:
        return ineligible(
            f"The next eligible reminder stage is R{next_stage}, not R{requested_stage}. "
            "Reminders must follow the R1 → R2 → R3 sequence."
        )

    # Rule 12: Duplicate prevention — stage already successfully sent
    if next_stage and any(a.status == "SENT" and a.stage == next_stage for a in attempts):
        return ineligible(
            f"R{next_stage} has already been successfully sent. "
            "Duplicate reminders are not permitted."
        )

    # Cancelled invoice
    if invoice.status == "CANCELLED":
        return ineligible("This invoice has been cancelled.")

    return eligible()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _active_promise(
    promises: list[PaymentPromise], today: date
) -> Optional[PaymentPromise]:
    return next(
        (p for p in promises if p.status == "ACTIVE" and p.promised_date >= today),
        None,
    )


def _open_review(
    reviews: list[PaymentReview], review_type: str
) -> Optional[PaymentReview]:
    return next(
        (r for r in reviews if r.review_type == review_type and r.status == "OPEN"),
        None,
    )

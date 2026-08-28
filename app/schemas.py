from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class EventType(StrEnum):
    payment_failed = "payment_failed"
    subscription_halted = "subscription_halted"
    invoice_overdue = "invoice_overdue"
    checkout_abandoned = "checkout_abandoned"


class InterventionType(StrEnum):
    new_payment_link = "new_payment_link"
    switch_payment_method = "switch_payment_method"
    nudge = "nudge"
    discount = "discount"
    manual_charge = "manual_charge"
    escalate = "escalate"


class TriageCategory(StrEnum):
    retryable_transient = "retryable_transient"
    customer_fixable = "customer_fixable"
    auth_failure = "auth_failure"
    risk_flagged = "risk_flagged"
    customer_abandoned = "customer_abandoned"
    unclassified = "unclassified"
    not_applicable = "not_applicable"


class GuardrailVerdict(StrEnum):
    approved = "approved"
    rejected = "rejected"
    escalated = "escalated"


class OutcomeStatus(StrEnum):
    recovered = "recovered"
    failed = "failed"
    unattributed_recovery = "unattributed_recovery"
    escalated = "escalated"
    unresolved = "unresolved"
    superseded = "superseded"


class StrategistProposal(BaseModel):
    intervention_type: InterventionType
    reasoning: str
    discount_pct: float | None = None
    draft_message: str | None = None


class CriticVerdict(BaseModel):
    grounded: bool
    issue: str | None = None


class RevenueAtRiskEvent(BaseModel):
    razorpay_event_id: str | None = None
    event_type: EventType
    source: str
    razorpay_entity_id: str
    customer_id: str | None = None
    failure_reason: str | None = None
    amount: int | None = None
    currency: str = "INR"
    raw_payload: dict
    detected_at: datetime

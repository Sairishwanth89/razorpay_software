import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Decision, Event
from app.razorpay_client import client
from app.schemas import EventType, GuardrailVerdict, InterventionType, StrategistProposal

BOUNDS = {
    EventType.payment_failed: {
        "max_attempts": 3,
        "cooldown_hours": 6,
        "discount_max_pct": 10,
        "discount_min_attempt": 2,
        "discount_max_amount": 500_000,
        "escalate_amount_threshold": 2_500_000,
        "close_as": "escalated",
    },
    EventType.subscription_halted: {
        "max_attempts": 2,
        "cooldown_hours": 24,
        "discount_max_pct": 0,
        "discount_min_attempt": None,
        "discount_max_amount": 0,
        "escalate_amount_threshold": 1_000_000,
        "close_as": "escalated",
    },
    EventType.invoice_overdue: {
        "max_attempts": 3,
        "cooldown_hours": 144,
        "discount_max_pct": 5,
        "discount_min_attempt": 3,
        "discount_max_amount": 5_000_000,
        "escalate_amount_threshold": 10_000_000,
        "close_as": "escalated",
    },
    EventType.checkout_abandoned: {
        "max_attempts": 2,
        "cooldown_hours": 2,
        "discount_max_pct": 5,
        "discount_min_attempt": 2,
        "discount_max_amount": 500_000,
        "escalate_amount_threshold": None,
        "close_as": "unresolved",
    },
}

ALLOWED_INTERVENTIONS = {
    EventType.payment_failed: [
        InterventionType.new_payment_link,
        InterventionType.switch_payment_method,
        InterventionType.discount,
        InterventionType.escalate,
    ],
    EventType.subscription_halted: [
        InterventionType.nudge,
        InterventionType.manual_charge,
        InterventionType.escalate,
    ],
    EventType.invoice_overdue: [
        InterventionType.nudge,
        InterventionType.discount,
        InterventionType.escalate,
    ],
    EventType.checkout_abandoned: [
        InterventionType.nudge,
        InterventionType.discount,
        InterventionType.escalate,
    ],
}

MAX_CONSECUTIVE_REJECTIONS = 2
TOTAL_DISCOUNT_CAP_PCT = 3
RESOLVED_LIVE_STATUSES = {"paid", "captured", "active", "completed", "cancelled"}


def check_preflight(event: Event, attempt_number: int, last_decision_at: datetime | None):
    """Cheap checks before ever calling Strategist. Returns (outcome, reason) to short-circuit, or None to proceed.
    outcome is one of: "cooldown", "escalated", "unresolved"."""
    bounds = BOUNDS[EventType(event.event_type)]

    threshold = bounds["escalate_amount_threshold"]
    if threshold and event.amount and event.amount > threshold:
        return "escalated", f"amount {event.amount} exceeds escalate threshold {threshold}"

    if attempt_number > bounds["max_attempts"]:
        return bounds["close_as"], f"attempt {attempt_number} exceeds max_attempts {bounds['max_attempts']}"

    if last_decision_at is not None:
        cooldown = timedelta(hours=bounds["cooldown_hours"])
        if datetime.now(UTC) - last_decision_at < cooldown:
            return "cooldown", "cooldown window has not elapsed since the last attempt"

    return None


async def has_concurrent_intervention(session: AsyncSession, event: Event) -> bool:
    if not event.customer_id:
        return False
    other_open = (
        await session.execute(
            select(Event).where(
                Event.customer_id == event.customer_id,
                Event.id != event.id,
                Event.status == "decided",
            )
        )
    ).scalars().all()
    return len(other_open) > 0


async def compute_discount_totals(session: AsyncSession) -> tuple[float, float]:
    all_events = (await session.execute(select(Event))).scalars().all()
    total_at_risk = sum(e.amount or 0 for e in all_events)

    approved_discounts = (
        await session.execute(
            select(Decision, Event)
            .join(Event, Decision.event_id == Event.id)
            .where(Decision.intervention_type == InterventionType.discount.value)
            .where(Decision.guardrail_verdict == GuardrailVerdict.approved.value)
        )
    ).all()
    total_discount_used = 0.0
    for decision, event in approved_discounts:
        pct = (decision.bounds_snapshot or {}).get("discount_pct")
        if pct and event.amount:
            total_discount_used += event.amount * pct / 100
    return total_discount_used, total_at_risk


async def fetch_live_status(event: Event) -> str | None:
    event_type = event.event_type
    if event_type == EventType.payment_failed.value:
        order_id = (
            event.raw_payload.get("payload", {}).get("payment", {}).get("entity", {}).get("order_id")
        )
        if not order_id:
            return None
        order = await asyncio.to_thread(client.order.fetch, order_id)
        return order.get("status")
    if event_type == EventType.checkout_abandoned.value:
        order = await asyncio.to_thread(client.order.fetch, event.razorpay_entity_id)
        return order.get("status")
    if event_type == EventType.invoice_overdue.value:
        invoice = await asyncio.to_thread(client.invoice.fetch, event.razorpay_entity_id)
        return invoice.get("status")
    if event_type == EventType.subscription_halted.value:
        subscription = await asyncio.to_thread(client.subscription.fetch, event.razorpay_entity_id)
        return subscription.get("status")
    return None


async def validate_proposal(
    event: Event,
    proposal: StrategistProposal,
    attempt_number: int,
    total_discount_used: float,
    total_at_risk: float,
) -> tuple[GuardrailVerdict, str]:
    bounds = BOUNDS[EventType(event.event_type)]
    allowed = ALLOWED_INTERVENTIONS[EventType(event.event_type)]

    if proposal.intervention_type not in allowed:
        return GuardrailVerdict.rejected, f"{proposal.intervention_type} not allowed for {event.event_type}"

    if proposal.intervention_type == InterventionType.discount:
        min_attempt = bounds["discount_min_attempt"]
        if min_attempt is None or attempt_number < min_attempt:
            return GuardrailVerdict.rejected, "discount not allowed at this attempt number"
        if not event.amount or event.amount > bounds["discount_max_amount"]:
            return GuardrailVerdict.rejected, "amount exceeds discount-eligible ceiling"
        if proposal.discount_pct is None or proposal.discount_pct > bounds["discount_max_pct"]:
            return GuardrailVerdict.rejected, "discount percentage exceeds cap"
        proposed_amount = event.amount * proposal.discount_pct / 100
        if total_at_risk > 0:
            projected_pct = (total_discount_used + proposed_amount) / total_at_risk * 100
            if projected_pct > TOTAL_DISCOUNT_CAP_PCT:
                return GuardrailVerdict.rejected, "batch-wide discount cap would be exceeded"

    live_status = await fetch_live_status(event)
    if live_status in RESOLVED_LIVE_STATUSES:
        return GuardrailVerdict.rejected, f"live state already resolved: {live_status}"

    return GuardrailVerdict.approved, "passed all checks"

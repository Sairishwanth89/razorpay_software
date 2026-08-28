from datetime import UTC, datetime

from app.config import logical_delta
from app.razorpay_client import client
from app.schemas import EventType, RevenueAtRiskEvent

ACTIONABLE_WEBHOOK_EVENTS = {
    "payment.failed": EventType.payment_failed,
    "subscription.halted": EventType.subscription_halted,
    "invoice.expired": EventType.invoice_overdue,
}

ABANDONMENT_THRESHOLD_HOURS = 0.5


def normalize_webhook(event_name: str, payload: dict) -> RevenueAtRiskEvent | None:
    event_type = ACTIONABLE_WEBHOOK_EVENTS.get(event_name)
    if event_type is None:
        return None

    detected_at = datetime.now(UTC)
    entities = payload.get("payload", {})

    if event_type is EventType.payment_failed:
        entity = entities["payment"]["entity"]
        return RevenueAtRiskEvent(
            event_type=event_type,
            source="webhook",
            razorpay_entity_id=entity["id"],
            customer_id=entity.get("customer_id"),
            failure_reason=entity.get("error_reason"),
            amount=entity.get("amount"),
            currency=entity.get("currency", "INR"),
            raw_payload=payload,
            detected_at=detected_at,
        )

    if event_type is EventType.subscription_halted:
        entity = entities["subscription"]["entity"]
        payment_entity = entities.get("payment", {}).get("entity", {})
        return RevenueAtRiskEvent(
            event_type=event_type,
            source="webhook",
            razorpay_entity_id=entity["id"],
            customer_id=entity.get("customer_id"),
            failure_reason=payment_entity.get("error_reason"),
            amount=payment_entity.get("amount") or entity.get("amount"),
            currency=payment_entity.get("currency", "INR"),
            raw_payload=payload,
            detected_at=detected_at,
        )

    entity = entities["invoice"]["entity"]
    return RevenueAtRiskEvent(
        event_type=EventType.invoice_overdue,
        source="webhook",
        razorpay_entity_id=entity["id"],
        customer_id=entity.get("customer_id"),
        failure_reason=None,
        amount=entity.get("amount"),
        currency=entity.get("currency", "INR"),
        raw_payload=payload,
        detected_at=detected_at,
    )


def poll_overdue_invoices() -> list[RevenueAtRiskEvent]:
    # Fallback for invoice.expired: in test mode Razorpay's expiry sweep can lag its own
    # expire_by timestamp by a long, undocumented margin, so we don't rely on the webhook
    # alone. Real wall-clock, not the logical demo clock - expire_by is a real absolute
    # timestamp Razorpay set, not something we control via a compressed demo delta.
    now = datetime.now(UTC)
    invoices = client.invoice.all({"count": 100})

    candidates = []
    for invoice in invoices["items"]:
        if invoice.get("status") != "issued":
            continue
        expire_by = invoice.get("expire_by")
        if not expire_by or datetime.fromtimestamp(expire_by, tz=UTC) > now:
            continue
        candidates.append(
            RevenueAtRiskEvent(
                event_type=EventType.invoice_overdue,
                source="poller",
                razorpay_entity_id=invoice["id"],
                customer_id=invoice.get("customer_id"),
                failure_reason=None,
                amount=invoice.get("amount"),
                currency=invoice.get("currency", "INR"),
                raw_payload=invoice,
                detected_at=now,
            )
        )
    return candidates


def poll_abandoned_orders() -> list[RevenueAtRiskEvent]:
    cutoff = datetime.now(UTC) - logical_delta(ABANDONMENT_THRESHOLD_HOURS)
    orders = client.order.all({"count": 100})

    candidates = []
    for order in orders["items"]:
        if order.get("status") != "created":
            continue
        if (order.get("amount_paid") or 0) > 0:
            continue
        created_at = datetime.fromtimestamp(order["created_at"], tz=UTC)
        if created_at > cutoff:
            continue
        candidates.append(
            RevenueAtRiskEvent(
                event_type=EventType.checkout_abandoned,
                source="poller",
                razorpay_entity_id=order["id"],
                customer_id=None,
                failure_reason=None,
                amount=order.get("amount"),
                currency=order.get("currency", "INR"),
                raw_payload=order,
                detected_at=datetime.now(UTC),
            )
        )
    return candidates

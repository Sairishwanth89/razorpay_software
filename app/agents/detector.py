from datetime import UTC, datetime, timedelta

from app.razorpay_client import client
from app.schemas import EventType, RevenueAtRiskEvent

ACTIONABLE_WEBHOOK_EVENTS = {
    "payment.failed": EventType.payment_failed,
    "subscription.halted": EventType.subscription_halted,
    "invoice.expired": EventType.invoice_overdue,
}

ABANDONMENT_THRESHOLD_MINUTES = 30


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


def poll_abandoned_orders() -> list[RevenueAtRiskEvent]:
    cutoff = datetime.now(UTC) - timedelta(minutes=ABANDONMENT_THRESHOLD_MINUTES)
    orders = client.order.all({"count": 100})

    candidates = []
    for order in orders["items"]:
        if order.get("status") != "created":
            continue
        if order.get("amount_paid", 0) > 0:
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

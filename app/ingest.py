from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bus import publish
from app.models import AuditLog, Event
from app.schemas import RevenueAtRiskEvent


async def ingest_event(
    session: AsyncSession,
    event: RevenueAtRiskEvent,
    razorpay_event_id: str | None,
) -> bool:
    if razorpay_event_id is not None:
        duplicate = await session.scalar(
            select(Event).where(Event.razorpay_event_id == razorpay_event_id)
        )
    else:
        duplicate = await session.scalar(
            select(Event).where(
                Event.razorpay_entity_id == event.razorpay_entity_id,
                Event.event_type == event.event_type.value,
                Event.status == "open",
            )
        )
    if duplicate is not None:
        return False

    db_event = Event(
        razorpay_event_id=razorpay_event_id,
        event_type=event.event_type.value,
        source=event.source,
        razorpay_entity_id=event.razorpay_entity_id,
        customer_id=event.customer_id,
        failure_reason=event.failure_reason,
        amount=event.amount,
        currency=event.currency,
        raw_payload=event.raw_payload,
        status="open",
        detected_at=event.detected_at,
        created_at=datetime.now(UTC),
    )
    session.add(db_event)
    session.add(
        AuditLog(
            event_id=None,
            actor="detector",
            action=f"event_detected:{event.event_type.value}",
            detail={"razorpay_entity_id": event.razorpay_entity_id, "source": event.source},
            created_at=datetime.now(UTC),
        )
    )
    await session.commit()
    await publish("triage", db_event.id)
    return True

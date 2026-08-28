from datetime import UTC, datetime

from app.agents.triage import classify_payment_failure
from app.bus import consume, publish
from app.db import async_session
from app.models import AuditLog, Event
from app.schemas import EventType, TriageCategory


async def triage_loop() -> None:
    while True:
        event_id = await consume("triage")
        try:
            await process_event(event_id)
        except Exception as exc:
            async with async_session() as session:
                session.add(
                    AuditLog(
                        event_id=event_id,
                        actor="triage",
                        action="processing_error",
                        detail={"error": str(exc)},
                        created_at=datetime.now(UTC),
                    )
                )
                await session.commit()


async def process_event(event_id: int) -> None:
    async with async_session() as session:
        db_event = await session.get(Event, event_id)
        if db_event is None:
            return

        if db_event.event_type == EventType.payment_failed.value:
            error_description = (
                db_event.raw_payload.get("payload", {})
                .get("payment", {})
                .get("entity", {})
                .get("error_description")
            )
            category, reasoning = await classify_payment_failure(
                db_event.failure_reason, error_description
            )
        else:
            category = TriageCategory.not_applicable
            reasoning = "non-payment-failure event, no root-cause diagnosis needed"

        db_event.root_cause_category = category.value
        db_event.status = "triaged"
        session.add(
            AuditLog(
                event_id=db_event.id,
                actor="triage",
                action=f"classified:{category.value}",
                detail={"reasoning": reasoning},
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()
        await publish("strategist", db_event.id)

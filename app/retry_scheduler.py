import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from app.agents.guardrail import BOUNDS
from app.agents.learner import record_attempt_failure
from app.bus import publish
from app.channel_agent import channel_topic
from app.config import ensure_utc, logical_delta
from app.db import async_session
from app.models import AuditLog, Decision, Event
from app.schemas import EventType

POLL_INTERVAL_SECONDS = 5


async def retry_scheduler_loop() -> None:
    while True:
        try:
            await requeue_ready_events()
        except Exception as exc:
            async with async_session() as session:
                session.add(
                    AuditLog(
                        event_id=None,
                        actor="scheduler",
                        action="processing_error",
                        detail={"error": str(exc)},
                        created_at=datetime.now(UTC),
                    )
                )
                await session.commit()
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def requeue_ready_events() -> None:
    """Events sit in "executed" status after an attempt until either an outcome webhook
    resolves them (app.agents.learner.handle_recovery_webhook) or their cooldown elapses -
    at which point the attempt is scored a failure and Guardrail's own max_attempts/
    escalation logic (already in app.channel_agent) should get another chance to run.
    This is the clock tick that drives that: requeue events whose logical cooldown has
    passed back onto their own channel's topic."""
    ready: list[tuple[int, str]] = []

    async with async_session() as session:
        events = (await session.execute(select(Event).where(Event.status == "executed"))).scalars().all()

        for event in events:
            last_decision = (
                await session.execute(
                    select(Decision)
                    .where(Decision.event_id == event.id)
                    .order_by(Decision.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if last_decision is None:
                continue

            bounds = BOUNDS[EventType(event.event_type)]
            if datetime.now(UTC) - ensure_utc(last_decision.created_at) < logical_delta(bounds["cooldown_hours"]):
                continue

            await record_attempt_failure(session, event, last_decision)

            event.status = "triaged"
            session.add(
                AuditLog(
                    event_id=event.id,
                    actor="scheduler",
                    action="requeued_for_next_attempt",
                    detail={},
                    created_at=datetime.now(UTC),
                )
            )
            ready.append((event.id, event.event_type))

        await session.commit()

    for event_id, event_type in ready:
        await publish(channel_topic(event_type), event_id)

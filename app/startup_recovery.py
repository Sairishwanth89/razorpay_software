from sqlalchemy import select

from app.bus import publish
from app.channel_agent import channel_topic
from app.db import async_session
from app.models import Decision, Event
from app.schemas import GuardrailVerdict


async def republish_in_flight_work() -> None:
    """The event bus (app.bus) is a plain in-process asyncio.Queue - it does not survive
    a process restart. Anything that was mid-pipeline (published to a topic but not yet
    consumed) when the process last stopped is lost from the queue, even though its
    Event/Decision rows are still sitting in the DB at a non-terminal status. Without this,
    those events wait forever for a message that will never arrive. Run once at startup,
    before the worker loops start consuming."""
    async with async_session() as session:
        open_events = (
            await session.execute(select(Event.id, Event.event_type).where(Event.status == "open"))
        ).all()
        triaged_events = (
            await session.execute(select(Event.id, Event.event_type).where(Event.status == "triaged"))
        ).all()
        decided_events = (await session.execute(select(Event).where(Event.status == "decided"))).scalars().all()

        decided_decision_ids = []
        for event in decided_events:
            last_approved = (
                await session.execute(
                    select(Decision)
                    .where(
                        Decision.event_id == event.id,
                        Decision.guardrail_verdict == GuardrailVerdict.approved.value,
                    )
                    .order_by(Decision.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if last_approved is not None:
                decided_decision_ids.append(last_approved.id)

    for event_id, event_type in [*open_events, *triaged_events]:
        await publish(channel_topic(event_type), event_id)
    for decision_id in decided_decision_ids:
        await publish("executor", decision_id)

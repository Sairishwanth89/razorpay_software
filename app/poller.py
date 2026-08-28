import asyncio
from datetime import UTC, datetime

from app.agents.detector import poll_abandoned_orders
from app.db import async_session
from app.ingest import ingest_event
from app.models import AuditLog

POLL_INTERVAL_SECONDS = 60


async def poller_loop() -> None:
    while True:
        try:
            candidates = await asyncio.wait_for(asyncio.to_thread(poll_abandoned_orders), timeout=30)
            async with async_session() as session:
                for candidate in candidates:
                    await ingest_event(session, candidate, razorpay_event_id=None)
        except Exception as exc:
            async with async_session() as session:
                session.add(
                    AuditLog(
                        event_id=None,
                        actor="poller",
                        action="processing_error",
                        detail={"error": str(exc)},
                        created_at=datetime.now(UTC),
                    )
                )
                await session.commit()
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

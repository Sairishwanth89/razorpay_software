import asyncio

from app.agents.detector import poll_abandoned_orders
from app.db import async_session
from app.ingest import ingest_event

POLL_INTERVAL_SECONDS = 60


async def poller_loop() -> None:
    while True:
        try:
            candidates = await asyncio.to_thread(poll_abandoned_orders)
            async with async_session() as session:
                for candidate in candidates:
                    await ingest_event(session, candidate, razorpay_event_id=None)
        except Exception:
            pass
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

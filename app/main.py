import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.channel_agent import CHANNEL_NAMES, channel_loop
from app.db import init_db
from app.executor_worker import executor_loop
from app.poller import poller_loop
from app.retry_scheduler import retry_scheduler_loop
from app.startup_recovery import republish_in_flight_work
from app.webhooks import router as webhooks_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await republish_in_flight_work()
    tasks = [
        asyncio.create_task(poller_loop()),
        *(asyncio.create_task(channel_loop(event_type)) for event_type in CHANNEL_NAMES),
        asyncio.create_task(executor_loop()),
        asyncio.create_task(retry_scheduler_loop()),
    ]
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="AI Revenue Recovery Agent", lifespan=lifespan)
app.include_router(webhooks_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import init_db
from app.decision_worker import decision_loop
from app.poller import poller_loop
from app.triage_worker import triage_loop
from app.webhooks import router as webhooks_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    tasks = [
        asyncio.create_task(poller_loop()),
        asyncio.create_task(triage_loop()),
        asyncio.create_task(decision_loop()),
    ]
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="AI Revenue Recovery Agent", lifespan=lifespan)
app.include_router(webhooks_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

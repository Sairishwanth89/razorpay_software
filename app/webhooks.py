import hashlib
import hmac
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException, Request

from app.agents.detector import normalize_webhook
from app.config import settings
from app.db import async_session
from app.ingest import ingest_event
from app.models import AuditLog

router = APIRouter()


def verify_signature(raw_body: bytes, signature: str) -> bool:
    expected = hmac.new(
        settings.razorpay_webhook_secret.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/webhooks/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(...),
    x_razorpay_event_id: str | None = Header(default=None),
):
    raw_body = await request.body()

    if not verify_signature(raw_body, x_razorpay_signature):
        raise HTTPException(status_code=400, detail="invalid signature")

    payload = json.loads(raw_body)
    event_name = payload.get("event", "")

    async with async_session() as session:
        session.add(
            AuditLog(
                event_id=None,
                actor="detector",
                action=f"webhook_received:{event_name}",
                detail={"event_id": x_razorpay_event_id},
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()

        at_risk_event = normalize_webhook(event_name, payload)
        if at_risk_event is not None:
            await ingest_event(session, at_risk_event, x_razorpay_event_id)

    return {"status": "ok"}

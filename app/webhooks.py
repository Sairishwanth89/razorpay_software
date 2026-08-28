import hashlib
import hmac
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException, Request

from app.agents.detector import normalize_webhook
from app.agents.learner import RECOVERY_WEBHOOK_EVENTS, handle_recovery_webhook
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
        async with async_session() as session:
            session.add(
                AuditLog(
                    event_id=None,
                    actor="detector",
                    action="webhook_rejected:invalid_signature",
                    detail={
                        "event_id": x_razorpay_event_id,
                        "reason": "HMAC-SHA256 signature did not match - payload discarded, nothing ingested",
                    },
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()
        raise HTTPException(status_code=400, detail="invalid signature")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        async with async_session() as session:
            session.add(
                AuditLog(
                    event_id=None,
                    actor="detector",
                    action="webhook_rejected:malformed_body",
                    detail={"event_id": x_razorpay_event_id, "reason": str(exc)},
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()
        raise HTTPException(status_code=400, detail="malformed payload")

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

        try:
            if event_name in RECOVERY_WEBHOOK_EVENTS:
                await handle_recovery_webhook(session, event_name, payload)
                await session.commit()
            else:
                at_risk_event = normalize_webhook(event_name, payload)
                if at_risk_event is not None:
                    await ingest_event(session, at_risk_event, x_razorpay_event_id)
        except Exception as exc:
            await session.rollback()
            session.add(
                AuditLog(
                    event_id=None,
                    actor="detector",
                    action=f"webhook_processing_error:{event_name}",
                    detail={"event_id": x_razorpay_event_id, "error": str(exc)},
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()
            # Signature was valid and the shape was legible - a downstream processing bug
            # shouldn't make us tell Razorpay to redeliver forever. Ack it, log it, move on.

    return {"status": "ok"}

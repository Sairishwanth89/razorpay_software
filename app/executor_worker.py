from datetime import UTC, datetime

from app.agents.executor import execute_intervention
from app.bus import consume
from app.db import async_session
from app.models import Action, AuditLog, Decision, Event
from app.schemas import GuardrailVerdict


async def executor_loop() -> None:
    while True:
        decision_id = await consume("executor")
        try:
            await process_decision(decision_id)
        except Exception as exc:
            async with async_session() as session:
                session.add(
                    AuditLog(
                        event_id=None,
                        actor="executor",
                        action="processing_error",
                        detail={"decision_id": decision_id, "error": str(exc)},
                        created_at=datetime.now(UTC),
                    )
                )
                await session.commit()


async def process_decision(decision_id: int) -> None:
    async with async_session() as session:
        decision = await session.get(Decision, decision_id)
        if decision is None or decision.guardrail_verdict != GuardrailVerdict.approved.value:
            return

        event = await session.get(Event, decision.event_id)
        if event is None:
            return

        result = await execute_intervention(event, decision)

        session.add(
            Action(
                decision_id=decision.id,
                razorpay_artifact_id=result.razorpay_artifact_id,
                action_type=result.action_type,
                before_state=result.before_state,
                after_state=result.after_state,
                status=result.status,
                executed_at=datetime.now(UTC),
            )
        )

        event.status = "escalated" if decision.intervention_type == "escalate" else "executed"

        session.add(
            AuditLog(
                event_id=event.id,
                actor="executor",
                action=f"executed:{result.action_type}",
                detail={
                    "status": result.status,
                    "razorpay_artifact_id": result.razorpay_artifact_id,
                    "decision_id": decision.id,
                },
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()

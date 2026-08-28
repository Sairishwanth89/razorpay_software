from datetime import UTC, datetime

from sqlalchemy import select

from app.agents.guardrail import (
    BOUNDS,
    MAX_CONSECUTIVE_REJECTIONS,
    check_preflight,
    compute_discount_totals,
    has_concurrent_intervention,
    validate_proposal,
)
from app.agents.learner import record_terminal_outcome
from app.agents.strategist import propose_intervention
from app.bus import consume, publish
from app.db import async_session
from app.models import AuditLog, Decision, Event
from app.schemas import EventType, GuardrailVerdict


def _audit(event_id: int, actor: str, action: str, detail: dict) -> AuditLog:
    return AuditLog(event_id=event_id, actor=actor, action=action, detail=detail, created_at=datetime.now(UTC))


async def decision_loop() -> None:
    while True:
        event_id = await consume("strategist")
        try:
            await process_event(event_id)
        except Exception as exc:
            async with async_session() as session:
                session.add(
                    _audit(event_id, "guardrail", "processing_error", {"error": str(exc)})
                )
                await session.commit()


async def process_event(event_id: int) -> None:
    async with async_session() as session:
        event = await session.get(Event, event_id)
        if event is None or event.status != "triaged":
            return

        prior_decisions = (
            await session.execute(
                select(Decision).where(Decision.event_id == event.id).order_by(Decision.attempt_number)
            )
        ).scalars().all()
        attempt_number = len(prior_decisions) + 1
        last_decision_at = prior_decisions[-1].created_at if prior_decisions else None

        preflight = check_preflight(event, attempt_number, last_decision_at)
        if preflight is not None:
            outcome, reason = preflight
            if outcome == "cooldown":
                session.add(_audit(event.id, "guardrail", "cooldown_active", {"reason": reason}))
                await session.commit()
                return
            event.status = outcome
            session.add(_audit(event.id, "guardrail", f"closed:{outcome}", {"reason": reason}))
            await record_terminal_outcome(
                session, event, prior_decisions[-1].id if prior_decisions else None, outcome
            )
            await session.commit()
            return

        prior_types = [d.intervention_type for d in prior_decisions]
        proposal = None
        verdict = None
        reason = None

        for _ in range(MAX_CONSECUTIVE_REJECTIONS):
            proposal = await propose_intervention(event, attempt_number, prior_types)

            if await has_concurrent_intervention(session, event):
                verdict = GuardrailVerdict.rejected
                reason = "another open intervention is already active for this customer"
            else:
                total_discount_used, total_at_risk = await compute_discount_totals(session)
                verdict, reason = await validate_proposal(
                    event, proposal, attempt_number, total_discount_used, total_at_risk
                )

            decision = Decision(
                event_id=event.id,
                attempt_number=attempt_number,
                intervention_type=proposal.intervention_type.value,
                reasoning=proposal.reasoning,
                guardrail_verdict=verdict.value,
                guardrail_reasoning=reason,
                bounds_snapshot={
                    "discount_pct": proposal.discount_pct,
                    "draft_message": proposal.draft_message,
                    "bounds": BOUNDS[EventType(event.event_type)],
                },
                created_at=datetime.now(UTC),
            )
            session.add(decision)
            session.add(
                _audit(
                    event.id,
                    "strategist",
                    f"proposed:{proposal.intervention_type.value}",
                    {"reasoning": proposal.reasoning},
                )
            )
            session.add(_audit(event.id, "guardrail", f"verdict:{verdict.value}", {"reason": reason}))
            await session.commit()
            await session.refresh(decision)

            if verdict == GuardrailVerdict.approved:
                event.status = "decided"
                await session.commit()
                await publish("executor", decision.id)
                return

            prior_types.append(proposal.intervention_type.value)

        event.status = "escalated"
        session.add(
            _audit(event.id, "guardrail", "closed:escalated", {"reason": "2 consecutive rejected proposals"})
        )
        await record_terminal_outcome(session, event, decision.id if decision else None, "escalated")
        await session.commit()

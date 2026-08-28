from datetime import UTC, datetime

from sqlalchemy import select

from app.agents.critic import verify_proposal
from app.agents.guardrail import (
    BOUNDS,
    MAX_CONSECUTIVE_REJECTIONS,
    check_preflight,
    compute_discount_totals,
    validate_proposal,
)
from app.agents.learner import record_terminal_outcome
from app.agents.orchestrator import (
    arbitrate_contact_slot,
    arbitrate_discount_budget,
    arbitrate_escalation_slot,
    check_customer_suppression,
    expected_value,
)
from app.agents.strategist import propose_intervention
from app.agents.triage import classify_payment_failure
from app.bus import consume, publish
from app.db import async_session
from app.models import AuditLog, Decision, Event
from app.schemas import EventType, GuardrailVerdict, InterventionType, TriageCategory

# Each channel owns its own Detect->Triage->Strategize loop and runs concurrently with
# the others - a hang or slow patch in one channel (e.g. Receivables waiting on an
# invoice API call) cannot stall Payment Retry or Checkout Recovery.
CHANNEL_NAMES = {
    EventType.payment_failed: "Payment Retry Agent",
    EventType.subscription_halted: "Mandate Agent",
    EventType.invoice_overdue: "Receivables Negotiator Agent",
    EventType.checkout_abandoned: "Checkout Recovery Agent",
}

TERMINAL_OR_CLAIMED_STATUSES = ("decided", "executed", "recovered", "escalated", "unresolved", "superseded")


def channel_topic(event_type: EventType | str) -> str:
    value = event_type.value if isinstance(event_type, EventType) else event_type
    return f"channel:{value}"


def _audit(event_id: int | None, actor: str, action: str, detail: dict) -> AuditLog:
    return AuditLog(event_id=event_id, actor=actor, action=action, detail=detail, created_at=datetime.now(UTC))


async def channel_loop(event_type: EventType) -> None:
    topic = channel_topic(event_type)
    channel_name = CHANNEL_NAMES[event_type]
    while True:
        event_id = await consume(topic)
        try:
            await process_event(event_id, channel_name)
        except Exception as exc:
            async with async_session() as session:
                session.add(_audit(event_id, channel_name, "processing_error", {"error": str(exc)}))
                await session.commit()


async def _classify_if_needed(session, event: Event) -> None:
    """Runs once per event (guarded by root_cause_category already being set) - a
    requeued retry skips straight back to strategizing instead of re-triaging."""
    if event.root_cause_category is not None:
        return

    if event.event_type == EventType.payment_failed.value:
        error_description = (
            event.raw_payload.get("payload", {}).get("payment", {}).get("entity", {}).get("error_description")
        )
        category, reasoning = await classify_payment_failure(event.failure_reason, error_description)
    else:
        category = TriageCategory.not_applicable
        reasoning = "non-payment-failure event, no root-cause diagnosis needed"

    event.root_cause_category = category.value
    session.add(_audit(event.id, "triage", f"classified:{category.value}", {"reasoning": reasoning}))


async def _close_event(session, event: Event, outcome: str, reason: str, decision_id: int | None) -> None:
    if outcome == "escalated":
        granted, slot_reason = await arbitrate_escalation_slot(session)
        if not granted:
            session.add(_audit(event.id, "orchestrator", "escalation_slot_denied", {"reason": slot_reason}))
            outcome = "unresolved"
            reason = f"{reason}; downgraded from escalated - {slot_reason}"

    event.status = outcome
    session.add(_audit(event.id, "guardrail", f"closed:{outcome}", {"reason": reason}))
    await record_terminal_outcome(session, event, decision_id, outcome)


async def process_event(event_id: int, channel_name: str) -> None:
    async with async_session() as session:
        event = await session.get(Event, event_id)
        if event is None or event.status in TERMINAL_OR_CLAIMED_STATUSES:
            return

        session.add(_audit(event.id, channel_name, "claimed_event", {}))
        await _classify_if_needed(session, event)
        event.status = "triaged"
        await session.commit()

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
            await _close_event(
                session, event, outcome, reason, prior_decisions[-1].id if prior_decisions else None
            )
            await session.commit()
            return

        suppressed, suppression_reason = await check_customer_suppression(session, event)
        if suppressed:
            await _close_event(
                session,
                event,
                "escalated",
                f"orchestrator: {suppression_reason}",
                prior_decisions[-1].id if prior_decisions else None,
            )
            await session.commit()
            return

        prior_types = [d.intervention_type for d in prior_decisions]
        proposal = None
        verdict = None
        reason = None
        decision = None

        for _ in range(MAX_CONSECUTIVE_REJECTIONS):
            proposal = await propose_intervention(event, attempt_number, prior_types)

            critic_verdict = await verify_proposal(event, proposal, attempt_number, prior_types)
            session.add(
                _audit(
                    event.id,
                    "critic",
                    "verified" if critic_verdict.grounded else "flagged",
                    {"issue": critic_verdict.issue},
                )
            )

            if not critic_verdict.grounded:
                verdict = GuardrailVerdict.rejected
                reason = f"critic: {critic_verdict.issue}"
            else:
                proposed_value = await expected_value(session, event, proposal.intervention_type.value)
                slot_granted, slot_reason = await arbitrate_contact_slot(session, event, proposed_value)

                if not slot_granted:
                    verdict = GuardrailVerdict.rejected
                    reason = f"orchestrator: {slot_reason}"
                elif proposal.intervention_type == InterventionType.escalate:
                    # A Strategist-initiated escalation is still a claim on the same
                    # scarce human-ops capacity as the max-attempts/rejection-exhaustion
                    # closure path below - must clear the same slot check, or a proposal
                    # that goes straight for "escalate" bypasses the cap entirely.
                    esc_granted, esc_reason = await arbitrate_escalation_slot(session)
                    if not esc_granted:
                        verdict = GuardrailVerdict.rejected
                        reason = f"orchestrator: {esc_reason}"
                    else:
                        verdict, reason = await validate_proposal(event, proposal, attempt_number)
                elif proposal.intervention_type == InterventionType.discount:
                    total_discount_used, total_at_risk = await compute_discount_totals(session)
                    proposed_amount = (event.amount or 0) * (proposal.discount_pct or 0) / 100
                    budget_granted, budget_reason = await arbitrate_discount_budget(
                        session, total_discount_used, total_at_risk, proposed_amount
                    )
                    if not budget_granted:
                        verdict = GuardrailVerdict.rejected
                        reason = f"orchestrator: {budget_reason}"
                    else:
                        verdict, reason = await validate_proposal(event, proposal, attempt_number)
                else:
                    verdict, reason = await validate_proposal(event, proposal, attempt_number)

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
                    "critic_issue": critic_verdict.issue,
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

        await _close_event(
            session, event, "escalated", "2 consecutive rejected proposals", decision.id if decision else None
        )
        await session.commit()

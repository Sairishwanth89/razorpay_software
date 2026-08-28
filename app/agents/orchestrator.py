from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.learner import amount_bracket
from app.models import AuditLog, Decision, Event, LearnerArm, Outcome
from app.schemas import OutcomeStatus

# Channel agents hold an open claim on a customer's "contact slot" while in either of
# these statuses. "executed" claims cannot be preempted (the action already happened);
# "decided" claims can (approved but not yet fired by the Executor).
OPEN_CONTACT_STATUSES = ("decided", "executed")

TOTAL_DISCOUNT_CAP_PCT = 3

# Cap on simultaneously open human escalations a single batch can create. Not an
# arbitrary throttle for its own sake - a human ops team has finite bandwidth, so once
# the batch has handed off this many cases, further low-value candidates close as
# unresolved instead of piling onto a queue nobody's actioning yet.
MAX_CONCURRENT_ESCALATIONS = 15

# Beta(1,1) mean - the neutral prior an arm gets before it has any data.
DEFAULT_SUCCESS_RATE_PRIOR = 0.5

# Past real contact attempts (executed actions that did not lead to recovery) for this
# customer, across ANY of their other events, with zero recoveries ever, before further
# automated outreach is suppressed. Deliberately counts only "failed" outcomes - not
# amount-triggered immediate escalations, which reflect policy, not customer behavior.
SUPPRESSION_THRESHOLD = 3


def _audit(event_id: int | None, action: str, detail: dict) -> AuditLog:
    return AuditLog(event_id=event_id, actor="orchestrator", action=action, detail=detail, created_at=datetime.now(UTC))


async def _arm_success_rate(session: AsyncSession, failure_reason: str, intervention_type: str, bracket: str) -> float:
    arm = (
        await session.execute(
            select(LearnerArm).where(
                LearnerArm.failure_reason == failure_reason,
                LearnerArm.intervention_type == intervention_type,
                LearnerArm.amount_bracket == bracket,
            )
        )
    ).scalar_one_or_none()
    if arm is None or (arm.successes + arm.failures) == 0:
        return DEFAULT_SUCCESS_RATE_PRIOR
    return arm.successes / (arm.successes + arm.failures)


async def expected_value(session: AsyncSession, event: Event, intervention_type: str) -> float:
    """amount * this arm's learned success rate (or a neutral prior with no data yet).
    The shared currency every arbitration decision below ranks on."""
    rate = await _arm_success_rate(
        session, event.failure_reason or "not_applicable", intervention_type, amount_bracket(event.amount)
    )
    return (event.amount or 0) * rate


async def arbitrate_contact_slot(session: AsyncSession, event: Event, proposed_value: float) -> tuple[bool, str]:
    """Contact frequency is a scarce shared resource - a customer should not be hit by
    two channel agents at once. Ranks competing claims by expected recovered value
    instead of a blind first-come mutex, and can preempt an incumbent that's been
    approved but not yet executed (an already-executed action can't be undone, so a
    later, higher-value challenger simply loses in that case)."""
    if not event.customer_id:
        return True, "no customer_id to arbitrate on"

    incumbents = (
        await session.execute(
            select(Event).where(
                Event.customer_id == event.customer_id,
                Event.id != event.id,
                Event.status.in_(OPEN_CONTACT_STATUSES),
            )
        )
    ).scalars().all()
    if not incumbents:
        return True, "no competing claim"

    for incumbent in incumbents:
        incumbent_decision = (
            await session.execute(
                select(Decision)
                .where(Decision.event_id == incumbent.id, Decision.guardrail_verdict == "approved")
                .order_by(Decision.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        incumbent_value = (
            await expected_value(session, incumbent, incumbent_decision.intervention_type)
            if incumbent_decision
            else 0.0
        )

        if incumbent.status == "executed" or proposed_value <= incumbent_value:
            return False, (
                f"contact slot held by event {incumbent.id} "
                f"(expected value {incumbent_value:.0f} vs challenger {proposed_value:.0f}, "
                f"incumbent status={incumbent.status})"
            )

        # Challenger has strictly higher expected value and the incumbent hasn't fired
        # yet - preempt it. The Executor re-checks event.status before acting, so this
        # alone is enough to stop the incumbent's action from actually firing.
        incumbent.status = "superseded"
        session.add(
            Outcome(
                event_id=incumbent.id,
                decision_id=incumbent_decision.id if incumbent_decision else None,
                outcome=OutcomeStatus.superseded.value,
                amount_recovered=None,
                attributed=False,
                resolved_at=datetime.now(UTC),
            )
        )
        session.add(
            _audit(
                incumbent.id,
                "preempted",
                {
                    "preempted_by_event_id": event.id,
                    "incumbent_value": incumbent_value,
                    "challenger_value": proposed_value,
                },
            )
        )

    return True, f"granted contact slot (expected value {proposed_value:.0f})"


async def arbitrate_discount_budget(
    session: AsyncSession, total_discount_used: float, total_at_risk: float, proposed_amount: float
) -> tuple[bool, str]:
    """The batch-wide discount pool is a shared budget across every channel - stops the
    ₹-recovered number from being gamed by discounting everything into a sale."""
    if total_at_risk <= 0:
        return True, "no at-risk baseline yet"
    projected_pct = (total_discount_used + proposed_amount) / total_at_risk * 100
    if projected_pct > TOTAL_DISCOUNT_CAP_PCT:
        return False, f"batch-wide discount cap would be exceeded ({projected_pct:.1f}% > {TOTAL_DISCOUNT_CAP_PCT}%)"
    return True, f"within discount budget ({projected_pct:.1f}% <= {TOTAL_DISCOUNT_CAP_PCT}%)"


async def check_customer_suppression(session: AsyncSession, event: Event) -> tuple[bool, str]:
    """A customer who has ignored repeated real contact attempts across past events, with
    no recovery ever, shouldn't keep getting automated outreach - repeated unanswered
    contact risks annoying someone into churning rather than recovering them (mirrors
    Butter Payments' suppression logic). Suppressed customers route to human escalation
    instead of another automated attempt. Returns (suppressed, reason)."""
    if not event.customer_id:
        return False, "no customer_id to evaluate"

    past_outcomes = (
        await session.execute(
            select(Outcome)
            .join(Event, Outcome.event_id == Event.id)
            .where(Event.customer_id == event.customer_id, Event.id != event.id)
        )
    ).scalars().all()

    if not past_outcomes:
        return False, "no history for this customer"

    if any(o.outcome in (OutcomeStatus.recovered.value, OutcomeStatus.unattributed_recovery.value) for o in past_outcomes):
        return False, "customer has a successful recovery in their history"

    failed_attempts = sum(1 for o in past_outcomes if o.outcome == OutcomeStatus.failed.value)
    if failed_attempts >= SUPPRESSION_THRESHOLD:
        return True, (
            f"{failed_attempts} past contact attempts for this customer with no recovery ever - "
            "suppressing further automated outreach"
        )

    return False, f"only {failed_attempts} past failed attempts, below suppression threshold ({SUPPRESSION_THRESHOLD})"


async def arbitrate_escalation_slot(session: AsyncSession) -> tuple[bool, str]:
    open_escalations = (await session.execute(select(Event).where(Event.status == "escalated"))).scalars().all()
    if len(open_escalations) >= MAX_CONCURRENT_ESCALATIONS:
        return False, f"{len(open_escalations)} escalations already open, at cap ({MAX_CONCURRENT_ESCALATIONS})"
    return True, "escalation slot available"

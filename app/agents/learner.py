from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Action, AuditLog, Decision, Event, LearnerArm, Outcome
from app.schemas import OutcomeStatus

# Ceilings in paise, matching the ₹ brackets already used in the Guardrail bounds table.
AMOUNT_BRACKETS = [
    (500_000, "upto_5k"),
    (2_500_000, "5k_to_25k"),
    (10_000_000, "25k_to_1L"),
]
AMOUNT_BRACKET_ABOVE = "above_1L"

RECOVERY_WEBHOOK_EVENTS = {"payment_link.paid", "invoice.paid", "payment.captured", "subscription.charged"}
TERMINAL_STATUSES = {"escalated", "unresolved"}


def amount_bracket(amount: int | None) -> str:
    if amount is None:
        return "unknown"
    for ceiling, label in AMOUNT_BRACKETS:
        if amount <= ceiling:
            return label
    return AMOUNT_BRACKET_ABOVE


def _audit(event_id: int | None, action: str, detail: dict) -> AuditLog:
    return AuditLog(event_id=event_id, actor="learner", action=action, detail=detail, created_at=datetime.now(UTC))


async def _get_or_create_arm(
    session: AsyncSession, failure_reason: str, intervention_type: str, bracket: str
) -> LearnerArm:
    arm = (
        await session.execute(
            select(LearnerArm).where(
                LearnerArm.failure_reason == failure_reason,
                LearnerArm.intervention_type == intervention_type,
                LearnerArm.amount_bracket == bracket,
            )
        )
    ).scalar_one_or_none()
    if arm is None:
        arm = LearnerArm(
            failure_reason=failure_reason,
            intervention_type=intervention_type,
            amount_bracket=bracket,
            successes=0,
            failures=0,
            updated_at=datetime.now(UTC),
        )
        session.add(arm)
    return arm


async def record_bandit_result(session: AsyncSession, event: Event, decision: Decision, success: bool) -> None:
    arm = await _get_or_create_arm(
        session,
        event.failure_reason or "not_applicable",
        decision.intervention_type,
        amount_bracket(event.amount),
    )
    if success:
        arm.successes += 1
    else:
        arm.failures += 1
    arm.updated_at = datetime.now(UTC)


async def list_arms(session: AsyncSession) -> list[LearnerArm]:
    return list((await session.execute(select(LearnerArm))).scalars().all())


async def record_terminal_outcome(
    session: AsyncSession, event: Event, decision_id: int | None, outcome_label: str
) -> None:
    """Event-level closure (escalated/unresolved) for the exception list - not a bandit
    signal itself, since the retry scheduler already scored the last attempt's own
    "failed" outcome before this closure could ever be reached."""
    session.add(
        Outcome(
            event_id=event.id,
            decision_id=decision_id,
            outcome=outcome_label,
            amount_recovered=None,
            attributed=False,
            resolved_at=datetime.now(UTC),
        )
    )
    session.add(_audit(event.id, f"outcome:{outcome_label}", {"decision_id": decision_id}))


async def record_attempt_failure(session: AsyncSession, event: Event, decision: Decision) -> None:
    """An attempt is scored a failure the instant the next attempt is about to fire
    (cooldown elapsed, nothing happened) - called by the retry scheduler right before it
    requeues the event for another round."""
    session.add(
        Outcome(
            event_id=event.id,
            decision_id=decision.id,
            outcome=OutcomeStatus.failed.value,
            amount_recovered=None,
            attributed=False,
            resolved_at=datetime.now(UTC),
        )
    )
    await record_bandit_result(session, event, decision, success=False)
    session.add(
        _audit(
            event.id,
            "outcome:failed",
            {"decision_id": decision.id, "intervention_type": decision.intervention_type},
        )
    )


def _extract_recovery_signal(event_name: str, payload: dict) -> tuple[str | None, str | None, int | None]:
    """Returns (artifact_id, fallback_entity_id, amount_recovered)."""
    entities = payload.get("payload", {})
    payment = entities.get("payment", {}).get("entity", {})

    if event_name == "payment_link.paid":
        payment_link = entities.get("payment_link", {}).get("entity", {})
        return payment_link.get("id"), None, payment.get("amount") or payment_link.get("amount_paid")
    if event_name == "invoice.paid":
        invoice = entities.get("invoice", {}).get("entity", {})
        return invoice.get("id"), invoice.get("id"), payment.get("amount") or invoice.get("amount_paid")
    if event_name == "payment.captured":
        return None, payment.get("order_id"), payment.get("amount")
    if event_name == "subscription.charged":
        subscription = entities.get("subscription", {}).get("entity", {})
        return None, subscription.get("id"), payment.get("amount")
    return None, None, None


async def _find_matching_event(
    session: AsyncSession, artifact_id: str | None, fallback_entity_id: str | None
) -> tuple[Event | None, Decision | None]:
    """Prefers an exact Action artifact-ID match - the whole point of tagging every
    created artifact - and falls back to a direct entity-ID match for the genuinely
    ambiguous cases (a plain nudge, or a resend of an already-existing invoice/subscription)
    that created no new trackable object."""
    if artifact_id:
        action = (
            await session.execute(
                select(Action)
                .where(Action.razorpay_artifact_id == artifact_id)
                .order_by(Action.executed_at.desc())
            )
        ).scalars().first()
        if action is not None:
            decision = await session.get(Decision, action.decision_id)
            event = await session.get(Event, decision.event_id) if decision else None
            if event is not None:
                return event, decision

    if fallback_entity_id:
        event = (
            await session.execute(
                select(Event)
                .where(Event.razorpay_entity_id == fallback_entity_id)
                .order_by(Event.created_at.desc())
            )
        ).scalars().first()
        if event is not None:
            decision = (
                await session.execute(
                    select(Decision)
                    .where(Decision.event_id == event.id)
                    .order_by(Decision.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            return event, decision

    return None, None


async def handle_recovery_webhook(session: AsyncSession, event_name: str, payload: dict) -> None:
    artifact_id, fallback_entity_id, amount_recovered = _extract_recovery_signal(event_name, payload)
    event, decision = await _find_matching_event(session, artifact_id, fallback_entity_id)

    if event is None:
        session.add(
            _audit(
                None,
                "recovery_signal_unmatched",
                {"webhook": event_name, "artifact_id": artifact_id, "fallback_entity_id": fallback_entity_id},
            )
        )
        return

    already_recovered = (
        await session.execute(
            select(Outcome).where(
                Outcome.event_id == event.id,
                Outcome.outcome.in_(
                    [OutcomeStatus.recovered.value, OutcomeStatus.unattributed_recovery.value]
                ),
            )
        )
    ).scalars().first()
    if already_recovered is not None:
        session.add(_audit(event.id, "recovery_signal_duplicate", {"webhook": event_name}))
        return

    terminal_failed = event.status in TERMINAL_STATUSES
    outcome_label = OutcomeStatus.unattributed_recovery.value if terminal_failed else OutcomeStatus.recovered.value

    session.add(
        Outcome(
            event_id=event.id,
            decision_id=decision.id if decision else None,
            outcome=outcome_label,
            amount_recovered=amount_recovered,
            attributed=not terminal_failed,
            resolved_at=datetime.now(UTC),
        )
    )

    if not terminal_failed:
        event.status = "recovered"
        if decision is not None:
            await record_bandit_result(session, event, decision, success=True)

    session.add(
        _audit(event.id, f"outcome:{outcome_label}", {"amount_recovered": amount_recovered, "webhook": event_name})
    )

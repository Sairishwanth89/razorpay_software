import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.models import Decision, Event
from app.razorpay_client import client
from app.schemas import EventType, InterventionType

# Real Razorpay-side constraint, deliberately NOT scaled by the demo logical clock -
# Razorpay enforces this against actual wall-clock time regardless of our internal pacing.
PAYMENT_LINK_EXPIRY_HOURS = 48


@dataclass
class ExecutionResult:
    action_type: str
    status: str
    razorpay_artifact_id: str | None
    before_state: dict
    after_state: dict


async def _create_payment_link(event: Event, decision: Decision, discount_pct: float) -> ExecutionResult:
    if not event.amount:
        return ExecutionResult(
            action_type=f"payment_link_skipped:{decision.intervention_type}",
            status="failed",
            razorpay_artifact_id=None,
            before_state={},
            after_state={"error": "event has no amount, cannot create payment link"},
        )

    final_amount = round(event.amount * (1 - discount_pct / 100))
    before = {"original_amount": event.amount, "discount_pct": discount_pct}
    data = {
        "amount": final_amount,
        "currency": event.currency,
        "description": f"Complete your payment ({event.event_type}, ref {event.razorpay_entity_id})",
        "notify": {"sms": False, "email": False},
        "reminder_enable": False,
        "expire_by": int((datetime.now(UTC) + timedelta(hours=PAYMENT_LINK_EXPIRY_HOURS)).timestamp()),
        "notes": {
            "revenue_recovery_event_id": str(event.id),
            "decision_id": str(decision.id),
            "original_entity_id": event.razorpay_entity_id,
            "intervention_type": decision.intervention_type,
        },
    }
    try:
        link = await asyncio.to_thread(client.payment_link.create, data)
        return ExecutionResult(
            action_type=f"payment_link_created:{decision.intervention_type}",
            status="sent",
            razorpay_artifact_id=link["id"],
            before_state=before,
            after_state={
                "payment_link_id": link["id"],
                "short_url": link.get("short_url"),
                "amount": final_amount,
                "status": link.get("status"),
            },
        )
    except Exception as exc:
        return ExecutionResult(
            action_type=f"payment_link_create_failed:{decision.intervention_type}",
            status="failed",
            razorpay_artifact_id=None,
            before_state=before,
            after_state={"error": str(exc)},
        )


def _simulate_nudge(event: Event, decision: Decision) -> ExecutionResult:
    """No real SMS/WhatsApp gateway in demo scope - the drafted message is logged to the
    audit trail as the recoverable artifact instead of actually being dispatched."""
    message = (decision.bounds_snapshot or {}).get("draft_message") or "Reminder sent to customer."
    return ExecutionResult(
        action_type="nudge_simulated",
        status="simulated_sent",
        razorpay_artifact_id=None,
        before_state={},
        after_state={"channel": "sms_whatsapp_simulated", "message": message},
    )


async def _resend_invoice(event: Event, decision: Decision) -> ExecutionResult:
    try:
        result = await asyncio.to_thread(client.invoice.notify_by, event.razorpay_entity_id, "email")
        return ExecutionResult(
            action_type="invoice_notification_resent",
            status="sent" if result.get("success") else "failed",
            razorpay_artifact_id=event.razorpay_entity_id,
            before_state={},
            after_state={"notify_result": result},
        )
    except Exception as exc:
        return ExecutionResult(
            action_type="invoice_notification_failed",
            status="failed",
            razorpay_artifact_id=event.razorpay_entity_id,
            before_state={},
            after_state={"error": str(exc)},
        )


async def _resend_subscription_invoice(event: Event, decision: Decision) -> ExecutionResult:
    """Razorpay has no "force charge" API for a halted subscription without the customer
    present - manual recovery means resending the existing unpaid invoice/payment-update link."""
    try:
        invoices = await asyncio.to_thread(
            client.invoice.all, {"subscription_id": event.razorpay_entity_id, "count": 5}
        )
        pending = [
            inv for inv in invoices.get("items", []) if inv.get("status") in ("issued", "partially_paid")
        ]
        if not pending:
            return ExecutionResult(
                action_type="manual_charge_skipped",
                status="failed",
                razorpay_artifact_id=None,
                before_state={},
                after_state={"error": "no pending invoice found for this subscription"},
            )
        invoice = pending[0]
        result = await asyncio.to_thread(client.invoice.notify_by, invoice["id"], "email")
        return ExecutionResult(
            action_type="manual_charge_invoice_resent",
            status="sent" if result.get("success") else "failed",
            razorpay_artifact_id=invoice["id"],
            before_state={"invoice_status": invoice.get("status")},
            after_state={"notify_result": result},
        )
    except Exception as exc:
        return ExecutionResult(
            action_type="manual_charge_failed",
            status="failed",
            razorpay_artifact_id=None,
            before_state={},
            after_state={"error": str(exc)},
        )


def _escalate(event: Event, decision: Decision) -> ExecutionResult:
    return ExecutionResult(
        action_type="escalated_to_human",
        status="escalated",
        razorpay_artifact_id=None,
        before_state={},
        after_state={"reasoning": decision.reasoning},
    )


async def execute_intervention(event: Event, decision: Decision) -> ExecutionResult:
    intervention = InterventionType(decision.intervention_type)
    discount_pct = (decision.bounds_snapshot or {}).get("discount_pct") or 0

    if intervention == InterventionType.escalate:
        return _escalate(event, decision)

    if intervention in (InterventionType.new_payment_link, InterventionType.switch_payment_method):
        return await _create_payment_link(event, decision, discount_pct=0)

    if intervention == InterventionType.discount:
        return await _create_payment_link(event, decision, discount_pct=discount_pct)

    if intervention == InterventionType.manual_charge:
        return await _resend_subscription_invoice(event, decision)

    if intervention == InterventionType.nudge:
        if event.event_type == EventType.invoice_overdue.value:
            return await _resend_invoice(event, decision)
        return _simulate_nudge(event, decision)

    return ExecutionResult(
        action_type="unsupported_intervention",
        status="failed",
        razorpay_artifact_id=None,
        before_state={},
        after_state={"error": f"no executor handler for intervention_type={intervention.value}"},
    )

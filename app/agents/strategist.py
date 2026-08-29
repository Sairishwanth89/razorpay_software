import json

from openai import AsyncOpenAI

from app.agents.guardrail import ALLOWED_INTERVENTIONS
from app.config import settings
from app.models import Event
from app.schemas import EventType, InterventionType, StrategistProposal

openai_client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=30.0, max_retries=1)

STRATEGIST_MODEL = "gpt-4o-mini"

SYSTEM_PROMPT = """You are the Strategist in a revenue recovery pipeline. You PROPOSE an intervention
for one at-risk payment/subscription/invoice/cart event - you never execute anything yourself. A
deterministic Guardrail validates your proposal against hard bounds before any action is taken, and can
reject it - if that happens you will be asked to propose again with that context.

Rules:
- Only propose an intervention_type from the allowed_interventions list you are given. Never invent one.
- Only propose "discount" when it genuinely fits this attempt number and situation - it is not always
  allowed, and the Guardrail enforces the real caps, not you.
- If you propose "discount", set discount_pct to a specific number (do not exceed what is reasonable).
- If you propose "nudge", write draft_message as a short, warm, Hinglish (Hindi+English mix) reminder
  suitable for SMS/WhatsApp to an Indian customer. Keep it under 300 characters. Do not include links.
- If a prior proposal for this event was rejected, do not repeat the same rejected intervention_type.
- If nothing reasonable can be done, propose "escalate".
- You are given this customer's customer_trust label, built only from their own real past outcomes on
  other events: "new" (no history yet), "reliable" (recovered more often than not), "mixed", or "at_risk"
  (failed to recover more often than not). Use it only to calibrate tone, never to invent facts you weren't
  given: "reliable" can get a lighter, friendlier nudge; "at_risk" should get a neutral, factual tone with
  no over-promising and no stacking a discount on top of a repeated non-payment history; "new" and "mixed"
  get your default neutral tone.

Respond as strict JSON:
{"intervention_type": "...", "reasoning": "one or two sentences", "discount_pct": null or a number, "draft_message": null or "..."}
"""


async def propose_intervention(
    event: Event, attempt_number: int, prior_intervention_types: list[str], customer_trust: str = "new"
) -> StrategistProposal:
    allowed = [i.value for i in ALLOWED_INTERVENTIONS[EventType(event.event_type)]]
    user_content = {
        "event_type": event.event_type,
        "root_cause_category": event.root_cause_category,
        "failure_reason": event.failure_reason,
        "amount_paise": event.amount,
        "currency": event.currency,
        "attempt_number": attempt_number,
        "allowed_interventions": allowed,
        "prior_intervention_types_this_event": prior_intervention_types,
        "customer_trust": customer_trust,
    }

    response = await openai_client.chat.completions.create(
        model=STRATEGIST_MODEL,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_content)},
        ],
    )
    content = response.choices[0].message.content or "{}"
    try:
        parsed = json.loads(content)
        intervention_type = InterventionType(parsed.get("intervention_type"))
    except (ValueError, json.JSONDecodeError):
        return StrategistProposal(
            intervention_type=InterventionType.escalate,
            reasoning="strategist response could not be parsed",
        )

    return StrategistProposal(
        intervention_type=intervention_type,
        reasoning=parsed.get("reasoning", ""),
        discount_pct=parsed.get("discount_pct"),
        draft_message=parsed.get("draft_message"),
    )

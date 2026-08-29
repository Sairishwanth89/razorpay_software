import json

from openai import AsyncOpenAI

from app.agents.guardrail import ALLOWED_INTERVENTIONS, BOUNDS
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
- Your reasoning must be consistent with the attempt_number given below - never claim "this is the second
  attempt" or reference a prior nudge/attempt that prior_intervention_types_this_event does not show.
- "discount" is only eligible at all if discount_min_attempt is not null, attempt_number >= discount_min_attempt,
  and amount_paise <= discount_max_amount_paise (all given below) - if any of those fail, do not propose a
  discount, propose something else instead.
- If you do propose "discount", discount_pct must be a specific number no greater than discount_max_pct_this_event
  given below - that ceiling is authoritative for THIS event type only, never assume a number from a different
  event type or a generic retail-discount instinct. Justify the specific number from the reasoning, don't
  reflexively max out the ceiling.
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
    bounds = BOUNDS[EventType(event.event_type)]
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
        "discount_max_pct_this_event": bounds["discount_max_pct"],
        "discount_min_attempt": bounds["discount_min_attempt"],
        "discount_max_amount_paise": bounds["discount_max_amount"],
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

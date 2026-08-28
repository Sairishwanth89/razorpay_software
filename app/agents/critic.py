import json

from openai import AsyncOpenAI

from app.agents.guardrail import BOUNDS
from app.config import settings
from app.models import Event
from app.schemas import CriticVerdict, EventType, StrategistProposal

openai_client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=30.0, max_retries=1)

CRITIC_MODEL = "gpt-4o-mini"

SYSTEM_PROMPT = """You are the Critic in a revenue recovery pipeline - an adversarial second check that runs
before Guardrail signs off on the Strategist's proposal. You check two distinct things:

1. GROUNDING: is every factual claim in the Strategist's reasoning actually grounded in the event data you
are given below - nothing more? Flag any claim about the customer, payment, root cause, or situation that
is not present in the provided data, or a reasoning step that does not follow from it. You are NOT checking
policy/bounds compliance - a separate deterministic Guardrail does that.

2. OVER-CONCESSION: LLM negotiators are documented to over-concede - offering more generosity than a
situation actually earns, e.g. defaulting to the maximum allowed discount rather than justifying that
specific number. If the proposal is a discount, check whether the requested discount_pct is actually
justified by the stated reasoning, or looks like reflexive maximization against the ceiling you're given
below, with no real justification for going that high.

Default to grounded=true unless you can point to a SPECIFIC ungrounded claim or a SPECIFIC instance of
unjustified over-concession.

You must ALWAYS provide a one-sentence rationale, whether you pass or flag the proposal - this becomes part
of a human-readable audit trail, so write a plain-English sentence someone with no other context could read
and understand exactly what you checked and why you reached that verdict.

Respond as strict JSON:
{"grounded": true or false, "rationale": "one plain-English sentence explaining your verdict either way"}
"""


async def verify_proposal(
    event: Event, proposal: StrategistProposal, attempt_number: int, prior_intervention_types: list[str]
) -> CriticVerdict:
    # Must mirror exactly what the Strategist itself was given (app.agents.strategist) -
    # otherwise a true claim grounded in data the Critic wasn't shown (e.g. attempt_number)
    # reads as a hallucination when it isn't one.
    bounds = BOUNDS[EventType(event.event_type)]
    source_data = {
        "event_type": event.event_type,
        "root_cause_category": event.root_cause_category,
        "failure_reason": event.failure_reason,
        "amount_paise": event.amount,
        "currency": event.currency,
        "attempt_number": attempt_number,
        "prior_intervention_types_this_event": prior_intervention_types,
    }
    user_content = {
        "source_data_actually_fetched": source_data,
        "discount_ceiling_pct_if_applicable": bounds["discount_max_pct"],
        "strategist_proposal": {
            "intervention_type": proposal.intervention_type.value,
            "reasoning": proposal.reasoning,
            "discount_pct": proposal.discount_pct,
            "draft_message": proposal.draft_message,
        },
    }

    try:
        response = await openai_client.chat.completions.create(
            model=CRITIC_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(user_content)},
            ],
        )
    except Exception as exc:
        # A Critic outage should not silently pass proposals through unverified, but it
        # also shouldn't be indistinguishable from an actual grounding/over-concession finding.
        return CriticVerdict(grounded=False, rationale=f"critic call failed, treating as unverifiable: {exc}")

    content = response.choices[0].message.content or "{}"
    try:
        parsed = json.loads(content)
        rationale = parsed.get("rationale") or "critic returned no rationale"
        return CriticVerdict(grounded=bool(parsed.get("grounded", True)), rationale=rationale)
    except (ValueError, json.JSONDecodeError):
        return CriticVerdict(grounded=False, rationale="critic response could not be parsed")

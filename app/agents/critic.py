import json

from openai import AsyncOpenAI

from app.config import settings
from app.models import Event
from app.schemas import CriticVerdict, StrategistProposal

openai_client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=30.0, max_retries=1)

CRITIC_MODEL = "gpt-4o-mini"

SYSTEM_PROMPT = """You are the Critic in a revenue recovery pipeline. Your only job is to try to BREAK the
Strategist's proposal by checking whether every factual claim in its reasoning is actually grounded in the
event data you're given below - nothing more. You are NOT checking policy/bounds compliance (a separate
deterministic Guardrail does that) and you are NOT re-deciding what the intervention should be. You ARE
checking for hallucination: any claim about the customer, payment, root cause, or situation that is not
present in the provided data, or a reasoning step that does not follow from it.

Default to grounded=true unless you can point to a SPECIFIC claim that has no basis in the data given.

Respond as strict JSON:
{"grounded": true or false, "issue": null or "one sentence naming the specific ungrounded claim or gap"}
"""


async def verify_proposal(
    event: Event, proposal: StrategistProposal, attempt_number: int, prior_intervention_types: list[str]
) -> CriticVerdict:
    # Must mirror exactly what the Strategist itself was given (app.agents.strategist) -
    # otherwise a true claim grounded in data the Critic wasn't shown (e.g. attempt_number)
    # reads as a hallucination when it isn't one.
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
        # also shouldn't be indistinguishable from an actual hallucination finding.
        return CriticVerdict(grounded=False, issue=f"critic call failed, treating as unverifiable: {exc}")

    content = response.choices[0].message.content or "{}"
    try:
        parsed = json.loads(content)
        return CriticVerdict(grounded=bool(parsed.get("grounded", True)), issue=parsed.get("issue"))
    except (ValueError, json.JSONDecodeError):
        return CriticVerdict(grounded=False, issue="critic response could not be parsed")

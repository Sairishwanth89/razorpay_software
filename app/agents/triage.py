import json

from openai import AsyncOpenAI

from app.config import settings
from app.schemas import TriageCategory

openai_client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=30.0, max_retries=1)

CLASSIFICATION_MODEL = "gpt-4o-mini"

RULES: dict[str, TriageCategory] = {
    "payment_timed_out": TriageCategory.retryable_transient,
    "bank_technical_error": TriageCategory.retryable_transient,
    "gateway_technical_error": TriageCategory.retryable_transient,
    "insufficient_funds": TriageCategory.customer_fixable,
    "insufficient_fund": TriageCategory.customer_fixable,
    "card_expired": TriageCategory.customer_fixable,
    "incorrect_cvv": TriageCategory.customer_fixable,
    "debit_instrument_blocked": TriageCategory.customer_fixable,
    "debit_instrument_inactive": TriageCategory.customer_fixable,
    "card_not_enrolled": TriageCategory.customer_fixable,
    "card_disabled_for_online_payments": TriageCategory.customer_fixable,
    "transaction_limit_exceeded": TriageCategory.customer_fixable,
    "card_declined": TriageCategory.customer_fixable,
    "card_number_invalid": TriageCategory.customer_fixable,
    "authentication_failed": TriageCategory.auth_failure,
    "payment_risk_check_failed": TriageCategory.risk_flagged,
    "payment_cancelled": TriageCategory.customer_abandoned,
}

LLM_SYSTEM_PROMPT = (
    "Classify a Razorpay payment failure into exactly one category: "
    "retryable_transient (transient bank/gateway issue, safe to retry automatically), "
    "customer_fixable (customer needs to act - funds, expiry, card status), "
    "auth_failure (OTP/verification failed), "
    "risk_flagged (bank/gateway flagged as risky, do not blindly retry), "
    "customer_abandoned (customer backed out), "
    "or unclassified (none of the above fit). "
    'Respond as JSON: {"category": "...", "reasoning": "one sentence"}'
)


async def classify_payment_failure(
    reason: str | None, error_description: str | None
) -> tuple[TriageCategory, str]:
    if reason and reason in RULES:
        return RULES[reason], f"rule match on error_reason={reason}"

    response = await openai_client.chat.completions.create(
        model=CLASSIFICATION_MODEL,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": LLM_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"error_reason={reason!r}, error_description={error_description!r}",
            },
        ],
    )
    content = response.choices[0].message.content or "{}"
    try:
        parsed = json.loads(content)
        category = TriageCategory(parsed.get("category", "unclassified"))
        reasoning = parsed.get("reasoning", "llm classification")
    except (ValueError, json.JSONDecodeError):
        category = TriageCategory.unclassified
        reasoning = "llm response could not be parsed"
    return category, reasoning

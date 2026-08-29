# Recovery Mesh

A concurrent multi-agent system that watches Razorpay payment traffic for revenue at
risk — failed payments, halted subscriptions, abandoned checkouts, overdue invoices —
proposes a bounded recovery intervention, adversarially checks its own reasoning,
executes real Razorpay API calls, and learns which interventions actually work. Every
money-moving decision is explainable, capped by hard numeric bounds, and logged to an
audit trail.

Built and demoed against **real Razorpay test-mode data**: real failing test cards,
real abandoned Orders, real overdue Invoices, real signed webhooks — not staged JSON.

## The problem

10-15% of recurring payments fail on first attempt. Industry figures for 2026 put
$1-1.5M/year at risk per $10M ARR from first-attempt payment failures alone, and put
the ceiling for AI-driven dunning recovery at 65-80% of failed payments vs. 30-40% for
rule-based dunning ([source](https://www.stuut.ai/blog/top-dunning-software-for-saas-companies-2026)).
Razorpay itself has validated agentic recovery is worth building — it shipped **Agent
Studio** at FTX 2026 with a Subscription Recovery agent (now with outbound voice calls)
and an Abandoned Cart Conversion agent. Recovery Mesh covers that same ground plus two
channels not in that shipped lineup — one-off (non-subscription) payment-failure retry,
and B2B invoice/receivables follow-up — and arbitrates scarce resources (discount
budget, escalation slots, one-open-intervention-per-customer) *across* all four
channels competing for the same customers at once, which none of Razorpay's
single-purpose agents need to do. See [`PITCH.md`](PITCH.md) for the full competitive
case, demo script, and honest current numbers.

## Architecture

```
Webhook / Poller → Detector → [4 concurrent channel agents: Triage → Strategist → Critic]
                                        → Orchestrator/Guardrail (arbitrate + bound)
                                        → Executor (real Razorpay calls)
                                        → Learner (bandit update)
                                                ↓ bounds exceeded
                                          Escalation / human review
```

- **Detector** (`app/agents/detector.py`) — normalizes Razorpay webhooks and polls for
  signals Razorpay has no webhook for (abandoned Orders, overdue Invoices).
- **Triage** — rule-based off Razorpay's own structured `error.reason` codes; LLM only
  for ambiguous cases.
- **Strategist** (`app/agents/strategist.py`) — proposes one intervention from a
  whitelist bounded by what's actually callable per event type. Never asserts a fact
  about payment/customer state it wasn't handed from source data.
- **Critic** (`app/agents/critic.py`) — adversarial second check on the Strategist's
  claims and on over-concession, doubling as a Compliance & Tone Guardrail: flags any
  drafted customer message for false urgency, confirm-shaming, or bait-and-switch
  phrasing, mirroring Razorpay's own published dark-pattern prohibition.
- **Orchestrator/Guardrail** (`app/agents/orchestrator.py`, `app/agents/guardrail.py`)
  — deterministic, no LLM in the approval path. Hard per-event-type bounds, a
  batch-wide discount-spend cap, one-open-intervention-per-customer, an
  escalation-slot cap, and a live re-fetch of payment state immediately before
  approving.
- **Executor** (`app/agents/executor.py`) — the real API calls, full before/after
  state logged.
- **Learner** (`app/agents/learner.py`) — success-rate table keyed on
  `(failure_reason, intervention_type, amount_bracket)`, updated the instant a
  matching webhook confirms the outcome.
- **Trust Score** (`app/agents/orchestrator.py:customer_trust_score`) — a per-customer
  signal built only from that customer's own real recovered/failed outcomes on their
  other events, fed into the Strategist to calibrate tone. No fabricated signal: a
  customer with no resolved history gets a neutral "new" prior.

Every layer's bound is deterministic config, not LLM discretion — see the Guardrail
bounds table in [`CLAUDE.md`](CLAUDE.md). That design was arrived at independently,
before Razorpay published its own Agent Studio principles ("agents don't set prices or
invent discounts," "every action is validated before it executes," "agents escalate
rather than acting unilaterally") — see `PITCH.md` for the side-by-side.

## Quickstart

Requires Python 3.11+, a [Razorpay test-mode account](https://dashboard.razorpay.com/)
(API keys + a webhook secret), and an OpenAI API key.

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows; use `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt

cp .env.example .env          # fill in RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET /
                               # RAZORPAY_WEBHOOK_SECRET / OPENAI_API_KEY
```

Expose the webhook endpoint for Razorpay to call (Razorpay blocklists ngrok.io/loca.lt
— use [zrok](https://zrok.io) instead), then point a webhook in the Razorpay Dashboard
at `<tunnel-url>/webhooks/razorpay` for at least `payment.failed`,
`subscription.pending`, `subscription.halted`, `invoice.expired`, `payment_link.paid`,
`invoice.paid`, `payment.captured`, and `subscription.charged`.

```bash
uvicorn app.main:app --reload --port 8000     # API + all background agent loops
streamlit run dashboard.py                     # live dashboard, separate terminal
```

Generate a real test-mode batch with the scripts in `scripts/` (`generate_batch_orders.py`,
`generate_abandoned_carts.py`, `generate_overdue_invoices.py`) — each creates real
Razorpay test-mode objects using documented test-mode failure triggers, not synthetic
JSON.

## Status

All seven build phases in `CLAUDE.md` are complete: detection, triage, strategist +
guardrail, execution, learning, a batch run with dashboard, and demo polish (including
graceful handling of a deliberately forged webhook, audit-logged end to end). Current
batch numbers, known limitations, and the full demo script are in
[`PITCH.md`](PITCH.md) — stated honestly, including the cases the system could not
resolve.

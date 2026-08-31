# Recovery Mesh

A concurrent multi-agent system that watches Razorpay payment traffic for revenue at
risk — failed payments, halted subscriptions, abandoned checkouts, overdue invoices —
proposes a bounded recovery intervention, adversarially checks its own reasoning,
executes real Razorpay API calls, and learns which interventions actually work. Every
money-moving decision is explainable, capped by hard numeric bounds, and logged to an
audit trail.

Built and demoed against **real Razorpay test-mode data**: real failing test cards,
real abandoned Orders, real overdue Invoices, real signed webhooks — not staged JSON.

Built for the **Razorpay Buildathon — Track 3: AI Revenue Recovery**.

## Table of contents

- [The problem](#the-problem)
- [Why this, why now](#why-this-why-now)
- [Architecture](#architecture)
- [Guardrail bounds](#guardrail-bounds-v1)
- [Learner attribution model](#learner-attribution-model-v1)
- [Stack](#stack)
- [Quickstart](#quickstart)
- [Project layout](#project-layout)
- [Honest current numbers](#honest-current-numbers)
- [Known limitations](#known-limitations)
- [Status](#status)

## The problem

10–15% of recurring payments fail on first attempt. Industry figures for 2026 put
$1–1.5M/year at risk per $10M ARR from first-attempt payment failures alone, and put
the ceiling for AI-driven dunning recovery at 65–80% of failed payments vs. 30–40% for
rule-based dunning ([source](https://www.stuut.ai/blog/top-dunning-software-for-saas-companies-2026)).

Razorpay itself has validated agentic recovery is worth building — it shipped **Agent
Studio** at FTX 2026 (built on the Claude Agent SDK) with eight live agents, including
a Subscription Recovery agent (now with outbound voice calls) and an Abandoned Cart
Conversion agent. Recovery Mesh covers that same ground plus two channels not in that
shipped lineup — one-off (non-subscription) payment-failure retry, and B2B
invoice/receivables follow-up — and arbitrates scarce resources (discount budget,
escalation slots, one-open-intervention-per-customer) *across* all four channels
competing for the same customers at once, which none of Razorpay's single-purpose
agents currently need to do.

See [`PITCH.md`](PITCH.md) for the full competitive case, live demo script, and
current honest numbers with commentary.

## Why this, why now

Razorpay's own published Agent Studio governance principles — "agents don't set
prices or invent discounts," "every action is validated before it executes," "agents
escalate rather than acting unilaterally," full audit trail, no dark patterns — read
like a spec for the Guardrail this project already had. That design was arrived at
independently, before this research existed to confirm it: deterministic bounds with
zero LLM discretion in the approval path, hard discount ceilings pulled from config
rather than invented by the Strategist, mandatory human escalation when bounds are
exceeded, and a full audit trail for every decision.

Full backward-research case, sourcing, and the honest "where Razorpay is ahead of us"
section: [`PITCH.md`](PITCH.md#why-this-why-now-the-backward-research-case).

## Architecture

```
Webhook / Poller → Detector → [4 concurrent channel agents: Triage → Strategist → Critic]
                                        → Orchestrator/Guardrail (arbitrate + bound)
                                        → Executor (real Razorpay calls)
                                        → Learner (bandit update)
                                                ↓ bounds exceeded
                                          Escalation / human review
```

Six stages, four channels (one-off payment failure, subscription halted, checkout
abandoned, invoice overdue), all running on an in-process async event bus with 4
concurrent per-channel workers.

- **Detector** (`app/agents/detector.py`) — normalizes real Razorpay webhooks
  (`payment.failed`, `subscription.pending`, `subscription.halted`, `invoice.expired`,
  and the recovery-confirmation webhooks) into one internal event schema, and polls
  for signals Razorpay has no webhook for (abandoned Orders — standard Checkout has no
  abandoned-cart webhook — and overdue Invoices, whose `invoice.expired` webhook did
  not reliably fire in test mode; see [Known limitations](#known-limitations)).
  Dedupes on webhook `event.id` since Razorpay redelivers on non-2xx. Pure plumbing,
  zero LLM calls.
- **Triage** (`app/agents/triage.py`) — rule-based first: Razorpay returns structured
  `error.reason`/`error.code` fields on failed payments, used directly. LLM only
  engages for ambiguous/unstructured signals.
- **Strategist** (`app/agents/strategist.py`) — LLM-assisted. Proposes one
  intervention from a whitelist bounded by what's actually callable per event type —
  there is no generic "retry payment" API. Never asserts a fact about payment/customer
  state it wasn't hand fed from source data; is given its own event type's real
  discount ceiling, attempt bounds, and cooldown so it can't propose outside them.
- **Critic** (`app/agents/critic.py`) — adversarial second check, doubling as a
  **Compliance & Tone Guardrail**. Re-verifies the Strategist's claims against the same
  source data (catches ungrounded assertions), checks for over-concession, and flags
  any drafted customer message for false urgency, confirm-shaming, or bait-and-switch
  phrasing — mirroring Razorpay's own published dark-pattern prohibition. Always
  returns a rationale, pass or fail.
- **Orchestrator / Guardrail** (`app/agents/orchestrator.py`, `app/agents/guardrail.py`)
  — deterministic, zero LLM in the approval path. Validates the proposal against hard
  per-event-type bounds (see below), a batch-wide discount-spend cap,
  one-open-intervention-per-customer (expected-value-ranked preemption, never
  preempting an already-executed action), an escalation-slot cap, and per-customer
  suppression after repeated failed contact with no recovery. Re-fetches *live*
  payment state immediately before approving, run back-to-back with the Executor with
  no async gap — this is what kills stale/hallucinated context.
- **Executor** (`app/agents/executor.py`) — makes the real Razorpay API calls (new
  payment link, subscription retry, invoice reminder), logs full before/after state.
- **Learner** (`app/agents/learner.py`) — after outcome is known, updates a
  Bayesian/bandit success-rate table keyed on
  `(failure_reason, intervention_type, amount_bracket)`. Stays explainable and gives a
  literal "strategy shift after N cycles" artifact for the demo.
- **Trust Score** (`app/agents/orchestrator.py:customer_trust_score`) — a per-customer
  signal built only from that customer's own real recovered/failed outcomes on their
  *other* events (`new` / `reliable` / `mixed` / `at_risk`), computed fresh before
  every Strategist call and fed into it to calibrate tone. No fabricated signal — a
  customer with no resolved history gets a neutral "new" prior.

### Anti-hallucination principle

The LLM (Strategist) never asserts a *fact* about payment/customer state — it only
reasons over fields already fetched from the API and passed into its context. Any
claim it makes is checked against source data before Guardrail approval (the Critic's
grounding check). The Guardrail re-verifies live state right before execution.

### Guardrail principle

Every money-moving action must be: **explainable** (reasoning attached), **bounded**
(hard numeric/time limits, not LLM discretion), **gated** (deterministic Guardrail
approval required, no LLM in the approval decision itself).

## Guardrail bounds (v1)

Deterministic config the Guardrail loads — no LLM discretion in these numbers. Bounds
run on **logical/synthetic timestamps carried by each event**, not real wall-clock
waits (see `TIME_SCALE_SECONDS_PER_HOUR` below), so a multi-day batch can replay in a
live demo session.

| Event type | Max attempts | Cooldown | Discount | Escalate when |
|---|---|---|---|---|
| One-off payment failure | 3 payment-link sends | 6h logical | ≤10%, only from 2nd attempt, amount ≤ ₹5,000 | 3 attempts exhausted, or amount > ₹25,000 (skip straight to human) |
| Subscription `halted` | 2 (nudge, then manual invoice charge) | 24h logical | None — always escalate if a pricing change is needed | Both attempts exhausted, or MRR > ₹10,000/cycle |
| Invoice overdue | 3 reminders (day+1/+7/+14) | cadence-enforced | ≤5% early-settlement, only after 2nd reminder, invoice ≤ ₹50,000 | Day+14 unanswered → exception list; invoices > ₹1,00,000 escalate immediately |
| Checkout abandonment | 2 nudges | 2h logical | ≤5%, only on 2nd nudge, cart ≤ ₹5,000 | No human escalation (low stakes) — closes as unresolved after 2 |

Cross-cutting caps, evaluated across the whole batch, not just per event:

- **Total discount spend ≤ 3% of total at-risk revenue in the batch.** The Guardrail
  tracks a running total and rejects any discount that would breach it.
- **Max 1 concurrent open intervention per customer**, arbitrated by expected value; an
  already-executed action can never be preempted.
- **2 consecutive invalid/rejected Strategist proposals on the same event →
  auto-escalate.** Stops an infinite proposal/reject loop from burning LLM calls.

These are v1 defaults, not final — see the full table with rationale in
[`CLAUDE.md`](CLAUDE.md#guardrail-bounds-v1).

## Learner attribution model (v1)

Attribution is deterministic via artifact ID wherever possible — almost every
Strategist intervention creates a distinct trackable object (new Payment Link ID,
invoice charge attempt, subscription payment-update link), so success is known the
instant Detector sees the matching webhook carrying that ID.

1. **Success = event-driven.** The matching webhook (`payment_link.paid`,
   `invoice.paid`, `payment.captured`, `subscription.charged`) marks that attempt
   recovered immediately, and the bandit arm gets a success update right away.
2. **Failure is declared the instant the Guardrail's own bound forces a move-on** — the
   cooldown expires with nothing happening, or the artifact hits its own natural
   expiry, whichever comes first.
3. **Late/post-window payments still count toward total ₹ recovered**, but don't feed
   the bandit — tagged `unattributed_recovery` instead. Crediting a timed-out
   intervention to the bandit would be dishonest attribution, even though it costs the
   demo a cleaner "the bandit learned!" moment. See
   [Honest current numbers](#honest-current-numbers).
4. **Global batch-finalization cutoff: 21 logical days from initial detection** — set
   by the longest cadence in the system (invoice reminders to day+14, plus a week's
   grace for a late payment to land in the total).

Full model with rationale: [`CLAUDE.md`](CLAUDE.md#learner-attribution-model-v1).

## Stack

- **Backend**: Python + FastAPI, async throughout
- **Razorpay integration**: official Python SDK, real test-mode API calls
- **Event bus**: in-process async queue (architecturally event-driven regardless of
  infra weight — fine for demo scope)
- **Storage**: SQLite via SQLAlchemy (async) — tables for events, decisions, actions,
  outcomes, audit log, learner arms
- **Webhook receiver**: FastAPI endpoint + mandatory HMAC-SHA256 signature
  verification (`app/webhooks.py`) + a [zrok](https://zrok.io) tunnel for local dev
  (Razorpay blacklists ngrok.io/loca.lt for webhook delivery)
- **LLM**: OpenAI API for Strategist reasoning, Critic verification, and customer
  message drafting
- **Dashboard**: Streamlit, reading live from SQLite (`dashboard.py`) — events,
  agent decisions with reasoning shown, audit trail console, ₹ recovered, guardrail &
  compliance activity, customer trust distribution, learner bandit table

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

`.env` also controls `TIME_SCALE_SECONDS_PER_HOUR` — leave at `3600` (real-time) for
production-like behavior, or set it low (e.g. `5`) so the Guardrail bounds table's
multi-hour/day cooldowns compress into a single live demo session.

Expose the webhook endpoint for Razorpay to call, then point a webhook in the Razorpay
Dashboard (Account & Settings → Webhooks) at `<tunnel-url>/webhooks/razorpay` for at
least `payment.failed`, `subscription.pending`, `subscription.halted`,
`invoice.expired`, `payment_link.paid`, `invoice.paid`, `payment.captured`, and
`subscription.charged`.

```bash
# one terminal each:
uvicorn app.main:app --reload --port 8000      # API + all background agent loops
streamlit run dashboard.py                      # live dashboard
zrok share public http://127.0.0.1:8000         # tunnel, if testing real webhooks
```

Generate a real test-mode batch with the scripts in `scripts/` — each creates real
Razorpay test-mode objects using documented test-mode failure triggers, not synthetic
JSON:

```bash
python -m scripts.generate_batch_orders        # one-off payment failures via test cards
python -m scripts.generate_abandoned_carts     # abandoned checkout Orders
python -m scripts.generate_overdue_invoices    # overdue Invoices
python -m scripts.create_demo_moment           # one real order, for a live demo beat
```

Run from the project root with `-m` (not `python scripts/<file>.py` directly) so the
`app` package resolves correctly.

## Project layout

```
app/
  agents/
    detector.py       # webhook normalization + pollers, zero LLM
    triage.py          # rule-based root-cause classification, LLM fallback
    strategist.py       # LLM: proposes one bounded intervention
    critic.py            # LLM: adversarial grounding + compliance/tone check
    orchestrator.py       # deterministic: trust score, resource arbitration
    guardrail.py            # deterministic: bounds enforcement, approval gate
    executor.py               # real Razorpay API calls, before/after logging
    learner.py                 # bandit update on confirmed outcome
  channel_agent.py    # per-channel worker loop wiring the pipeline together
  webhooks.py         # signed webhook receiver
  poller.py           # background pollers (abandoned checkout, overdue invoice)
  retry_scheduler.py  # cooldown-based requeue for retries
  ingest.py           # event normalization + dedup entrypoint
  models.py           # SQLAlchemy models (events, decisions, actions, outcomes, audit)
  schemas.py          # Pydantic schemas (Strategist proposal, Critic verdict, ...)
  main.py             # FastAPI app + startup wiring

scripts/              # real test-mode batch generators + demo helpers
dashboard.py          # Streamlit live dashboard
CLAUDE.md             # full project spec (this file's source of truth)
PITCH.md              # competitive case, demo script, honest numbers
```

## Honest current numbers

As of the last committed batch run: **≈130 events tracked**, with recovery rate,
₹-at-risk, ₹-recovered, and the exception list all visible live on the dashboard's
stat tiles — deliberately not hardcoded here, since they change every time a new batch
runs. `PITCH.md`'s [honest numbers section](PITCH.md#the-honest-numbers-not-cherry-picked)
has a snapshot with commentary on *why* the recovery rate looks low (most of the batch
is synthetic abandoned-checkout data with no real person behind it) and which single
recovered payment is real, human-driven, and confirmed by an actual webhook.

## Known limitations

Said plainly rather than hidden — full detail in
[`PITCH.md`](PITCH.md#known-limitations-say-these-before-a-judge-finds-them):

- Learner bandit arms can show 0 successes in a batch where the only real recovery
  landed after its own attribution window closed — by design, not a bug (see the
  attribution model above).
- Invoice-overdue detection depends on a poller fallback, not `invoice.expired`,
  because that webhook did not reliably fire in Razorpay test mode.
- Bandit arms are keyed on `(failure_reason, intervention_type, amount_bracket)`, not
  `event_type` — some pooling across channels with the same failure reason.
- No "Promise-to-Pay" feature (customer replies "I'll pay Friday" → tracked
  commitment) — deliberately not built, because demoing it would require fabricating
  a specific customer's specific words, which this project treats as a hard line (see
  `PITCH.md`). A real Trust Score was built instead.
- The batch-wide escalation cap is a real, load-bearing limit — under real contention
  it correctly rejects some proposals into the exception list rather than overriding
  the cap, which is the Guardrail working as designed, not a shortfall.

## Status

All seven build phases are complete: detection, triage, strategist + guardrail,
execution, learning, a batch run with dashboard, and demo polish (including graceful
handling of a deliberately forged webhook, audit-logged end to end). Full build order
and phase breakdown: [`CLAUDE.md`](CLAUDE.md#build-order).

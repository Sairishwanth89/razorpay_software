# Project: AI Revenue Recovery Agent (Razorpay Buildathon — Track 3)

This file gives Claude Code the full context for this project. Read this before making changes.

## Event & Track

Razorpay Buildathon, **Track 3: AI Revenue Recovery**.

> Build an agent that detects revenue at risk, determines the right intervention, and executes a
> bounded recovery workflow: from payment failures and checkout abandonment to overdue receivables.
>
> **The bar:** Don't just identify the problem. Show measured money recovered across a batch, with
> compliant escalation, stopping rules, and an audit trail.

### Why this track (for context, not to re-litigate)
- Real, provable market (dunning/recovery is a proven SaaS category — Chargebee Retain, Butter, Recurly Recovery).
- Every ₹ recovered is a transaction Razorpay processes — incentives align with the platform, not just the merchant.
- Razorpay test mode has *documented* failure triggers (specific test cards → specific decline reasons), so
  recovery-rate numbers can be earned against real API behavior instead of staged data.
- Naturally event-driven and multi-agent — the track's own problem shape (detect → diagnose → intervene → recover)
  maps directly onto agent boundaries instead of us forcing an architecture onto a simple problem.

## Scope

**Primary:** Payment failure → subscription/checkout recovery.
**Secondary/stretch (same architecture, different event adapter):** B2B receivables chasing — overdue Invoices,
reminders, payment link resends. Building this second is the proof that the architecture generalizes.

Explicitly out of scope: chargebacks/disputes (different track), cross-border (different data shape, low ROI on time).

## Architecture

Six agents on an event bus, not a single monolithic chain:

```
Webhook/Poller → [Detector] → [Triage] → [Strategist] → [Guardrail] → [Executor] → [Learner]
                                                              ↓ (bounds exceeded)
                                                         [Escalation/Human]
```

- **Detector** — normalizes Razorpay webhooks (`payment.failed`, `subscription.pending`,
  `subscription.halted`, `invoice.expired`) plus a polled signal (Orders API: `created` status, no
  linked payment, past an age threshold → abandoned checkout — standard Checkout has no abandoned-cart
  webhook, only Magic Checkout does, and we're not integrating Magic Checkout) into one internal
  `RevenueAtRiskEvent` schema. Dedupes on webhook `event.id` since Razorpay redelivers on non-2xx.
  Pure plumbing, no LLM involved.
- **Triage** — classifies root cause. **Rule-based first**: Razorpay returns structured `error.reason` /
  `error.code` fields on failed payments — use those directly. LLM only engages for ambiguous/unstructured
  signals (e.g. summarizing repeated customer behavior patterns).
- **Strategist** — LLM-assisted. Proposes an intervention, constrained by what's actually callable per
  event type — there is no "retry payment" API. One-off payment failure → new Order + Payment Link.
  Subscription → manual invoice charge or payment-update link. Options: new payment link / switch payment
  method / send Hinglish nudge / bounded discount / escalate. For subscriptions, only proposes once the
  subscription is `halted` — Razorpay's own native retries (T+1/T+2/T+3, 4 attempts total) own the
  `pending` state, so the agent stays out of the way until those are exhausted. It *proposes*, never
  executes.
- **Guardrail** — deterministic, not LLM. Validates the proposal against hard bounds: max retries, cooldown
  windows, max discount %, and a `sendable_window` compliance check (TRAI DLT template / time-of-day rules
  for promotional messages — modeled and logged in the audit trail, no real SMS/WhatsApp gateway call in
  demo scope). Re-fetches *live* payment state immediately before approving, run back-to-back with Executor
  with no async gap in between — this is what kills stale/hallucinated context, not a separate
  "hallucination checker" agent.
- **Executor** — makes the real Razorpay API calls (new payment link, subscription retry, invoice reminder).
  Logs full before/after state for the audit trail.
- **Learner** — after outcome is known, updates a success-rate table keyed on
  `(failure_reason, intervention_type, amount_bracket)`. Simple Bayesian/bandit update — stays explainable,
  and gives a literal "strategy shift after N cycles" artifact for the demo.

### Anti-hallucination principle
LLM (Strategist) never asserts a *fact* about payment/customer state — it only reasons over fields already
fetched from the API and passed into its context. Any claim it makes gets checked against source data before
Guardrail approval (lightweight claim-vs-source diff). Guardrail re-verifies live state right before execution.

### Guardrail principle
Every money-moving action must be: **explainable** (reasoning attached), **bounded** (hard numeric/time limits,
not LLM discretion), **gated** (Guardrail approval required, deterministic code path, no LLM in the approval
decision itself).

## Guardrail bounds (v1)

Deterministic config the Guardrail agent loads — no LLM discretion in these numbers. Bounds run on
**logical/synthetic timestamps carried by each event**, not real wall-clock waits, so a multi-day batch
can replay in a live demo session; real wall-clock is a production concern, out of scope here.

| Event type | Max attempts | Cooldown | Discount | Escalate when |
|---|---|---|---|---|
| One-off payment failure | 3 payment-link sends | 6h logical | ≤10%, only from 2nd attempt, amount ≤ ₹5,000 | 3 attempts exhausted, or amount > ₹25,000 (skip straight to human) |
| Subscription `halted` | 2 (nudge, then manual invoice charge) | 24h logical | None — pricing decision, always escalate if requested | Both attempts exhausted, or MRR > ₹10,000/cycle |
| Invoice overdue | 3 reminders (day+1/+7/+14) | cadence-enforced | ≤5% early-settlement, only after 2nd reminder, invoice ≤ ₹50,000 | Day+14 unanswered → exception list; invoices > ₹1,00,000 escalate immediately, agent just tracks in parallel |
| Checkout abandonment | 2 nudges | 2h logical | ≤5%, only on 2nd nudge, cart ≤ ₹5,000 | No human escalation (low stakes) — closes as unresolved after 2 |

Cross-cutting caps, evaluated across the whole batch, not just per event:
- **Total discount spend ≤ 3% of total at-risk revenue in the batch.** Guardrail tracks a running total
  and rejects any discount that would breach it (forces escalate-or-no-discount). Stops the demo's
  ₹-recovered number from being gamed by discounting everything into a sale.
- **Max 1 concurrent open intervention per customer.** A customer with both a failed payment and an
  abandoned cart doesn't get hit by two parallel agent actions; the second is rejected until the first
  resolves or times out.
- **2 consecutive invalid/rejected Strategist proposals on the same event → auto-escalate.** Stops an
  infinite LLM proposal/reject loop from burning tokens live during the demo.

These are v1 defaults — sensible starting points, not final. Revisit once real batch numbers come in from
Phase 6.

## Learner attribution model (v1)

Same synthetic/logical clock as the Guardrail bounds above. Attribution is **deterministic via artifact
ID wherever possible** — almost every Strategist intervention creates a distinct trackable object (new
Payment Link ID, invoice charge attempt, subscription payment-update link), so success is known the
instant Detector sees the matching webhook (`payment_link.paid` / `invoice.paid` / `payment.captured` /
`subscription.charged`) carrying that ID — no time-window guessing, no risk of crediting the wrong arm.
A plain nudge with no new artifact is the only genuinely ambiguous case.

1. **Success = event-driven.** The matching webhook fires → that `(event_id, attempt_number,
   intervention_type)` is marked recovered immediately, and the bandit arm for
   `(failure_reason, intervention_type, amount_bracket)` gets a success update right away — doesn't wait
   for any window to close.
2. **Failure is declared at the same moment Guardrail's own bound already forces a move-on** — no
   separate timing number invented. An attempt is scored a failure the instant the next attempt fires
   (cooldown expired, nothing happened) or the artifact hits its own natural expiry (e.g. a 48h payment
   link), whichever comes first. Reuses the bounds table above instead of adding a parallel concept.
3. **Late/post-window payments still count toward total ₹ recovered, but don't feed the bandit.** If a
   customer pays after their arm was already scored a failure or after escalation, it's tagged
   `unattributed_recovery` — included in the batch's headline recovered-₹ number (don't hide money that
   came in), excluded from the Learner's per-arm update (crediting a timed-out intervention would be
   dishonest attribution).
4. **Global batch-finalization cutoff: 21 logical days from initial detection.** Set by the longest
   cadence in the system (invoice reminders run to day+14, plus a week's grace for a late self-serve
   payment to still land in the total-recovered bucket). Every other event type resolves or fails well
   inside this window — it exists only to guarantee the batch run terminates cleanly for the demo instead
   of leaving items pending forever.

## Stack

- Backend: Python + FastAPI (Razorpay's official Python SDK; async fits event-driven well)
- Event bus: in-process async queue is fine for demo scope — architecturally event-driven regardless of infra weight
- Storage: Postgres or SQLite — tables for events, decisions, outcomes, audit log
- Webhook receiver: FastAPI endpoint + HMAC-SHA256 signature verification (see Razorpay reference below) + tunnel (**zrok**, not ngrok — Razorpay blacklists ngrok.io/loca.lt for webhook delivery) for local dev
- LLM: OpenAI API for Strategist reasoning + customer message drafting
- Dashboard: simple (Streamlit or a small Next.js view) — live events, agent decisions with reasoning shown,
  audit trail, ₹ recovered, learning curve over the batch

## Build order

| Phase | Deliverable |
|---|---|
| 0 | Test account, API keys (test mode), webhook tunnel, project scaffold |
| 1 | Detector: webhook receiver + event generator (real failing test payments/invoices, not staged JSON) |
| 2 | Triage: rule-based classifier off Razorpay error codes |
| 3 | Strategist + Guardrail: bounded action whitelist, LLM proposal + deterministic validation |
| 4 | Executor: real retry/reminder API calls + audit logging |
| 5 | Learner: feedback loop, success-rate table, bandit update |
| 6 | Batch run (50+ records) + dashboard: recovery rate, ₹ recovered, exception list, audit trail |
| 7 | Demo polish: one deliberately-triggered failure handled gracefully end-to-end, narrative, pitch |

## Razorpay API reference (confirmed from docs)

- Gateway URL: `https://api.razorpay.com/v1` (some newer APIs on v2). Auth: HTTP Basic, `key_id:key_secret` base64.
- Keys: Dashboard → Account & Settings → API Keys. Separate Test (`rzp_test_...`) / Live keys.
- **Orders** `/orders` — create/fetch/update.
- **Payments** `/payments` — capture, fetch, fetch-by-order, update. Does not *collect* money on its own.
- **Refunds** `/refunds` — normal + instant, full/partial, batch.
- **Subscriptions** — plans, UPI Autopay, e-mandates, payment retry handling on failed auto-charge.
- **Invoices** — create/cancel/resend, states, webhooks — for the receivables-chaser stretch goal.
- **Payment Links / Payment Pages / QR Codes / Smart Collect** — alternate collection surfaces with their own
  create/fetch/cancel/batch APIs and webhooks.
- **Magic Checkout** — has an explicit abandoned-cart webhook + RTO Intelligence.
- **RazorpayX Payouts** — Contact → Fund Account → Payout (or Composite Payout API in one call). Idempotency
  key mandatory. `queue_if_low_balance` avoids hard failures. Relevant if receivables stretch needs disbursement.
- **Webhooks**: signed via `X-Razorpay-Signature` = HMAC-SHA256(raw request body, webhook secret), hex-encoded.
  Webhook secret is separate from the API key secret, set in Dashboard → Account & Settings → Webhooks.
  **Always verify this signature before trusting any webhook payload — this is the audit-trail foundation.**
  Hash the raw unparsed body, not a re-serialized/parsed version, or verification silently always fails.
- **Confirmed subscription lifecycle**: failed auto-charge → `subscription.pending` (Razorpay auto-retries
  next day, shifting for bank holidays) → after 4 total failed attempts → `subscription.halted` (invoices
  keep generating, no more auto-charge attempts). Not merchant-configurable. Manual recovery during either
  state: charge an existing unpaid invoice directly, or share a payment-update link.
- **Confirmed card `error.reason` enum** (Triage rule table): `insufficient_funds`, `card_expired`,
  `bank_technical_error`, `card_declined`, `authentication_failed`, `incorrect_cvv`,
  `debit_instrument_blocked`, `debit_instrument_inactive`, `card_not_enrolled`,
  `card_disabled_for_online_payments`, `payment_timed_out`, `transaction_limit_exceeded`,
  `payment_risk_check_failed`, `gateway_technical_error`, `payment_cancelled`, `payment_failed`.
  Full reference: https://razorpay.com/docs/errors/payments/cards/
- **Test-mode failure triggering**: pick a specific test card number (table renders via JS on the docs
  page — open in a browser, not fetchable as static text) to select network/scenario, choose "failure" on
  the mock success/failure screen, then enter an OTP under 4 digits to fail the authentication step.
- Test mode: specific test card numbers reproduce specific real decline reasons (insufficient funds, expired
  card, bank server down, etc.) — use these to build the "real, not staged" event batch. Test UPI IDs exist too.
- Official Razorpay MCP Server exists (`https://mcp.razorpay.com/mcp`) if we want an agent-facing tool interface
  layered on top later — not required for MVP.

## Success metrics to show at demo time

- ₹ recovered / ₹ at risk across the batch (honest, not cherry-picked)
- Recovery rate broken down by failure reason and intervention type
- Full audit trail for every action taken (what, why, when, bounds checked)
- At least one failure handled gracefully end-to-end, shown live
- Evidence of the Learner shifting strategy weights after N cycles (the self-learning proof point)
- Exception list: cases the system could not resolve, shown honestly, not hidden

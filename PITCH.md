# Recovery Mesh — Demo Narrative & Pitch

Razorpay Buildathon, Track 3: AI Revenue Recovery.

## The pitch, in one breath

Every failed payment, halted subscription, abandoned checkout, and overdue invoice is
revenue Razorpay already touched once and is about to lose for good. Recovery Mesh is a
concurrent multi-agent system that watches for that moment across four channels at once,
proposes a bounded intervention, adversarially checks its own reasoning before acting,
executes real Razorpay API calls, and learns which interventions actually work — with
every decision explainable, capped by hard numeric bounds, and logged to an audit trail
a compliance reviewer could read cold.

It is built and demoed against **real Razorpay test-mode data** — real failing test
cards, real abandoned Orders, real overdue Invoices, real webhooks — not staged JSON.

## Why this, why now (the backward-research case)

Razorpay shipped **Agent Studio** with a Subscription Recovery Agent and an Abandoned
Cart Conversion Agent. That's a signal, not a threat: the company has already validated
that agentic recovery is worth building internally. It also draws the map of where we
add value instead of duplicating it:

- **Direct overlap, by design:** our Mandate Agent (subscription) and Checkout Recovery
  channels cover the same ground Razorpay's own agents cover — we don't avoid it, we show
  our version is held to a stricter bar (adversarial verification, hard spend caps).
- **White space:** one-off payment-failure retry and B2B receivables chasing are not
  covered by Razorpay's shipped agents. That's real, unclaimed territory in the same
  product surface.
- **Structurally different from the rest of the market.** Chargebee Retain, Stripe
  Smart Retries, Recurly Recovery — all classical ML propensity scoring, not LLM
  reasoning. Nobody in this space runs an adversarial critic against its own proposals
  or arbitrates scarce resources (discount budget, contact slots, escalation capacity)
  across channels competing for the same customer. Those two pieces are the actual
  differentiators, not "we used an LLM."

## Architecture, one line per layer

```
Webhook / Poller → Detector → [4 concurrent channel agents: Triage → Strategist → Critic]
                                        → Orchestrator/Guardrail (arbitrate + bound)
                                        → Executor (real Razorpay calls)
                                        → Learner (bandit update)
                                                ↓ bounds exceeded
                                          Escalation / human review
```

- **Detector** — normalizes webhooks + polls for signals Razorpay has no webhook for
  (abandoned Orders, and — as of this session — overdue Invoices; see Known Limitations).
- **Triage** — rule-based off Razorpay's own structured `error.reason` codes; LLM only
  for ambiguous cases.
- **Strategist** — proposes one intervention from a whitelist bounded by what's actually
  callable per event type. Never asserts a fact about payment/customer state it wasn't
  handed.
- **Critic** — adversarial second check. Verifies the proposal's claims against the same
  source data the Strategist saw, and separately checks for LLM over-concession
  (documented failure mode in negotiation agents). Always returns a rationale, pass or fail.
- **Orchestrator/Guardrail** — deterministic, no LLM in the approval path. Enforces
  per-event-type bounds (max attempts, cooldowns, discount ceilings), a batch-wide
  discount-spend cap, one-open-intervention-per-customer, an escalation-slot cap, and
  per-customer suppression after repeated failed contact. Re-fetches live payment state
  immediately before approving — no async gap between check and execution.
- **Executor** — the real API calls (payment links, invoice reminders, subscription
  charges), full before/after state logged.
- **Learner** — Bayesian success-rate table keyed on (failure_reason, intervention_type,
  amount_bracket), updated the instant a matching webhook confirms the outcome.

## Live demo script

1. **Open the dashboard.** Point at the agent strip — four channels, live handled/in-flight
   counts, not a static screenshot.
2. **Point at the attention banner.** Cases sitting in `escalated` status with their ₹
   value — the system surfaces what needs a human instead of burying it in a table.
3. **Pick one event in "Follow one event end to end."** Walk the log top to bottom:
   triage → strategist proposal with reasoning → critic verdict with rationale →
   guardrail verdict → executor action. Every line is a real reasoning trace, not a
   status code.
4. **Trigger a forged webhook live** (already rehearsed this session):
   ```
   curl -X POST http://localhost:8000/webhooks/razorpay \
     -H "X-Razorpay-Signature: forged" \
     -d '{"event":"payment.failed", ...}'
   ```
   Show the 400 response, then show the rejection appear in the live agent log within a
   few seconds — `detector · webhook_rejected:invalid_signature` — proving the audit
   trail is grounded in the actual security boundary, not decorative.
5. **Show the recovery-rate and exception-list tables.** Say the honest number out loud
   (see below) before anyone can ask.
6. **Toggle light/dark mode** — same data, same story, not a cosmetic afterthought.

## The honest numbers (not cherry-picked)

As of this batch run: **87 events tracked, ₹20,08,749 at risk, ₹8,750 recovered
(0.4%).** That recovery rate looks low next to a SaaS dunning benchmark, and the
reason is structural, not a bug: the bulk of this batch is synthetic abandoned-checkout
Orders created to exercise the pipeline — there's no real human behind them who was ever
going to pay. The ₹8,750 that *did* come back is real: it's one of the live browser
checkouts we personally drove through the full pipeline this session (Strategist →
Critic → Guardrail → Executor), confirmed by an actual Razorpay webhook.

That single recovery is also the sharpest honesty point in the system: it landed
**after** its own intervention's attribution window had already closed, so per our own
attribution rules it counts toward the headline ₹-recovered figure but is explicitly
**excluded from the Learner's bandit update** — crediting a timed-out intervention would
be dishonest attribution, so the system doesn't do it, even though it costs the demo a
cleaner "look, the bandit learned!" moment.

## Known limitations (say these before a judge finds them)

- **Learner arms show 0 successes across the board in this batch.** Not hidden — see
  above for why, and see `learner_arms` for the raw table.
- **Invoice-overdue detection depended on the `invoice.expired` webhook, which Razorpay's
  test-mode expiry sweep did not fire even 65+ minutes past `expire_by`, with no
  documented SLA.** Fixed this session with a polling fallback mirroring the existing
  abandoned-checkout poller (`app/agents/detector.py:poll_overdue_invoices`) — the batch
  is now live end-to-end instead of silently stuck.
- **Bandit arms are keyed on (failure_reason, intervention_type, amount_bracket), not
  event_type** — a checkout nudge and an invoice nudge with the same "not_applicable"
  failure reason currently pool into the same arm. Worth splitting if this goes further;
  flagged honestly rather than fixed under demo-week time pressure.
- **17 checkout-abandonment escalations saturated the batch-wide escalation cap (15)**,
  so most of the invoice batch's own escalation attempts got correctly rejected and
  landed in the exception list as `unresolved` rather than overriding the cap. This is
  the guardrail working as designed under real contention, not a failure — but it means
  the invoice channel's headline numbers are mostly "declined honestly," not "recovered."

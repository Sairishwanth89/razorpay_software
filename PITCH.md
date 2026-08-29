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

Razorpay didn't just validate this problem space in theory — it shipped into it. At
FTX 2026 Razorpay launched **Agent Studio**, an AI-agent marketplace built on the
Claude Agent SDK, with eight live agents: Dispute Responder, Subscription Recovery
(now with outbound voice calls in English/Hindi via ElevenLabs), Abandoned Cart
Conversion (via marketplace partners SuperU and Nugget by Zomato), RTO Shield, RTO
Insights, Settlement Insights, and Cashflow Forecaster. That's the strongest possible
confirmation this is a problem the company is actively, seriously solving right now —
not a hackathon fiction.

Mapped against that real lineup, checked this session (2026-08-29), not assumed:

- **Direct overlap, and Razorpay is ahead here.** Their Subscription Recovery agent
  now places outbound voice calls — more mature than our text/link-based nudge. We
  don't pretend otherwise. What we show instead is the same channel held to a
  stricter, independently-arrived-at bar: adversarial self-checking (Critic) before
  Guardrail approval, and cross-channel resource arbitration Razorpay's single-purpose
  agents don't need to do.
- **Genuine, still-open white space.** One-off (non-subscription) payment-failure
  retry — new Order + Payment Link after a failed checkout — is not one of the eight
  shipped agents. B2B invoice/receivables follow-up is mentioned once, in passing, as
  a platform capability, but isn't a dedicated shipped agent either — soft white
  space, not a clean claim, and we say so plainly rather than oversell it.
- **The differentiator that survives contact with their real lineup.** Every one of
  Razorpay's eight agents is scoped to a single channel. None of them arbitrate a
  *shared, scarce* resource — discount budget, escalation slots, one-intervention-
  per-customer — across channels competing for the same customer at the same time.
  Recovery Mesh's Orchestrator/Guardrail does exactly that, live, under real
  contention (see the escalation-cap saturation in Known Limitations below — that's
  this exact mechanism firing for real, not a diagram).

**The part that matters most for standing on our own: we didn't know any of this when
we wrote Recovery Mesh's Guardrail.** Razorpay's own published Agent Studio
principles — "the merchant is always in control," "agents don't set prices or invent
discounts," "every action is validated before it executes," "every single action is
logged with a full audit trail," "agents escalate ... rather than acting
unilaterally," "no agent takes an irreversible action without explicit merchant
approval," plus a dark-pattern prohibition against false urgency and confirm-shaming —
read like a spec for the Guardrail we already built: deterministic bounds with zero
LLM discretion in the approval path, hard discount ceilings pulled from config rather
than invented by the Strategist, mandatory human escalation when bounds are exceeded,
and a full audit trail for every decision. We arrived at the same governance shape
independently, from first principles, before this research existed to confirm it.
That's the actual case for "standalone": this isn't shaped to fit a hackathon rubric,
it's shaped to clear the bar the platform itself now publishes.

(Sources: [Razorpay Agent Studio](https://razorpay.com/agent-studio/),
[Principles, Guardrails, and Merchant Control](https://razorpay.com/blog/razorpay-agent-studio-principles-guardrails-and-merchant-control/).)

Outside Razorpay's own ecosystem the market is still classical ML propensity
scoring — Chargebee Retain, Stripe Smart Retries, Recurly Recovery. 2026 industry
figures put AI-driven dunning at recovering 65-80% of failed payments vs. 30-40% for
rule-based dunning, and put $1-1.5M/year at risk per $10M ARR from first-attempt
payment failures alone ([Stuut.ai, 2026](https://www.stuut.ai/blog/top-dunning-software-for-saas-companies-2026)) —
the scale of problem this class of agent is built for, independent of which platform
ships it.

## Why Razorpay specifically benefits (not just the merchant)

Easy to pitch this as "helps merchants recover lost sales" and stop there. From
Razorpay's own seat as the platform, the incentive is sharper than that:

- **It's revenue to Razorpay too, not just goodwill for merchants.** Razorpay earns
  its fee on every processed transaction. A recovered payment link, a reactivated
  subscription, a paid invoice isn't just money saved for the merchant — it's a
  transaction that flows back through Razorpay's own rails and gets charged again.
  Recovery Mesh is a volume-generation tool for Razorpay wearing a merchant-retention
  costume.
- **It fits their marketplace strategy exactly, at the moment it's open.** Agent
  Studio launched with named "Build/Launch Partners" (SuperU, Nugget by Zomato) already
  publishing agents into it — Razorpay wants an ecosystem around its rails rather than
  building every vertical in-house, the same platform playbook as any app store. Their
  eight shipped agents don't include one-off (non-subscription) payment-failure retry,
  and receivables/invoice chasing is only mentioned in passing, not shipped. Recovery
  Mesh sits in exactly that gap — shaped like a candidate marketplace submission, not a
  one-off hackathon toy.
- **It's a lower-risk reference implementation of the bar they just publicly set.**
  Razorpay's own Agent Studio principles say agents can't set prices or invent
  discounts, every action is validated before execution, agents escalate rather than
  act unilaterally, and everything is audit-logged. Certifying third-party marketplace
  agents against that bar is Razorpay's own review cost. A submission that already
  enforces those exact constraints deterministically — our Guardrail has zero LLM
  discretion in the approval path — is cheaper for them to trust and certify than one
  they'd have to audit from scratch.
- **Cross-channel arbitration is a gap in their current portfolio, not a feature.**
  Every one of Razorpay's eight agents is scoped to a single channel. Nothing in their
  shipped lineup stops a Subscription Recovery call and an Abandoned Cart nudge from
  both hitting the same customer, or two agents from separately blowing a shared
  discount budget. That's infrastructure Razorpay would want sitting *underneath* all
  of its agents, not a feature it would build per-agent — and it's exactly what
  Recovery Mesh's Orchestrator/Guardrail already does, live, under real contention (see
  Known Limitations below).

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
- **Critic** — adversarial second check, doubling as a **Compliance & Tone Guardrail**.
  Verifies the proposal's claims against the same source data the Strategist saw, checks
  for LLM over-concession (documented failure mode in negotiation agents), and — as of
  this session — flags any drafted customer message for false urgency, confirm-shaming,
  or bait-and-switch phrasing, mirroring Razorpay's own published dark-pattern
  prohibition. Always returns a rationale, pass or fail, on every check.
- **Orchestrator/Guardrail** — deterministic, no LLM in the approval path. Enforces
  per-event-type bounds (max attempts, cooldowns, discount ceilings), a batch-wide
  discount-spend cap, one-open-intervention-per-customer, an escalation-slot cap, and
  per-customer suppression after repeated failed contact. Re-fetches live payment state
  immediately before approving — no async gap between check and execution.
- **Executor** — the real API calls (payment links, invoice reminders, subscription
  charges), full before/after state logged.
- **Learner** — Bayesian success-rate table keyed on (failure_reason, intervention_type,
  amount_bracket), updated the instant a matching webhook confirms the outcome.
- **Trust Score** (Orchestrator) — a per-customer signal built only from that customer's
  own real recovered/failed outcomes on their *other* events (`new` with no history,
  `reliable`, `mixed`, `at_risk`), computed fresh before every Strategist call and fed
  into it to calibrate tone — a reliable customer gets a lighter nudge, an at_risk one
  gets a neutral, no-over-promising message. Zero fabricated signal: silent "new" prior
  until a customer has real resolved history.

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
- **We deliberately did not build a "Promise-to-Pay" feature** (customer replies "I'll
  pay Friday" → tracked commitment → auto-follow-up), even though it was proposed this
  session. There is no real two-way messaging channel in this build — nudges are
  `nudge_simulated` — so making PTP demoable would mean an operator manually typing a
  customer's "reply" into the dashboard. That fabricates a specific customer's specific
  words and intent, a different and worse kind of staged than anything else here (which
  is honest about infrastructure gaps, never about customer behavior). We built the
  Trust Score instead — same "learning made visible" demo value, zero fabricated data.
- **17 checkout-abandonment escalations saturated the batch-wide escalation cap (15)**,
  so most of the invoice batch's own escalation attempts got correctly rejected and
  landed in the exception list as `unresolved` rather than overriding the cap. This is
  the guardrail working as designed under real contention, not a failure — but it means
  the invoice channel's headline numbers are mostly "declined honestly," not "recovered."

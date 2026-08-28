import sqlite3
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.config import settings

st.set_page_config(page_title="AI Revenue Recovery Agent", layout="wide", page_icon="✨")

DB_PATH = settings.database_url.split("///")[-1]

TERMINAL_STATUSES = ("recovered", "escalated", "unresolved")

# ---- Validated palette (see the dataviz skill's references/palette.md) ----
STATUS_COLORS = {
    "recovered": "#0ca30c",  # status: good
    "escalated": "#fab219",  # status: warning
    "unresolved": "#ec835a",  # status: serious
    "superseded": "#898781",  # neutral / administrative, not good or bad
    "open": "#9ec5f4",  # in-flight, sequential-blue steps (not yet resolved)
    "triaged": "#6da7ec",
    "decided": "#3987e5",
    "executed": "#2a78d6",
}
STATUS_LABELS = {
    "recovered": "Recovered",
    "escalated": "Escalated",
    "unresolved": "Unresolved",
    "superseded": "Superseded",
    "open": "Open",
    "triaged": "Triaged",
    "decided": "Decided",
    "executed": "Executed",
}
CHANNEL_COLORS = {
    "payment_failed": "#2a78d6",  # categorical slot 1
    "checkout_abandoned": "#eb6834",  # slot 2
    "invoice_overdue": "#1baf7a",  # slot 3
    "subscription_halted": "#eda100",  # slot 4
}
CHANNEL_LABELS = {
    "payment_failed": "Payment Retry",
    "checkout_abandoned": "Checkout Recovery",
    "invoice_overdue": "Receivables Negotiator",
    "subscription_halted": "Mandate Agent",
}
SEQUENTIAL_RAMP = ("#cde2fb", "#0d366b")  # sequential blue, 100 -> 700
FONT_STACK = "system-ui, -apple-system, 'Segoe UI', sans-serif"

st.markdown(
    """
<style>
:root {
  --surface-1: #fcfcfb;
  --text-primary: #0b0b0b;
  --text-secondary: #52514e;
  --text-muted: #898781;
  --border: rgba(11,11,11,0.10);
  --success-text: #006300;
}
@media (prefers-color-scheme: dark) {
  :root {
    --surface-1: #1a1a19;
    --text-primary: #ffffff;
    --text-secondary: #c3c2b7;
    --text-muted: #898781;
    --border: rgba(255,255,255,0.10);
    --success-text: #0ca30c;
  }
}
.block-container { padding-top: 2rem; max-width: 1400px; }
.hero-card {
  background: var(--surface-1);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 22px 26px;
  margin-bottom: 18px;
}
.hero-label { font-size: 14px; color: var(--text-secondary); margin-bottom: 6px; }
.hero-figure {
  font-size: 52px;
  font-weight: 600;
  color: var(--text-primary);
  line-height: 1.1;
}
.hero-sub { font-size: 15px; color: var(--success-text); margin-top: 8px; font-weight: 500; }
.stat-tile {
  background: var(--surface-1);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px 18px;
  height: 100%;
}
.stat-tile .label { font-size: 13px; color: var(--text-secondary); margin-bottom: 6px; }
.stat-tile .value { font-size: 26px; font-weight: 600; color: var(--text-primary); }
.stat-tile .delta { font-size: 13px; color: var(--text-muted); margin-top: 4px; }
.section-title { font-size: 18px; font-weight: 600; color: var(--text-primary); margin: 6px 0 2px 0; }
.section-caption { font-size: 13px; color: var(--text-secondary); margin-bottom: 12px; }
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_data(ttl=3)
def load_tables():
    db_uri = Path(DB_PATH).resolve().as_uri()
    con = sqlite3.connect(f"{db_uri}?mode=ro", uri=True, timeout=5)
    try:
        events = pd.read_sql_query("SELECT * FROM events", con)
        decisions = pd.read_sql_query("SELECT * FROM decisions", con)
        actions = pd.read_sql_query("SELECT * FROM actions", con)
        outcomes = pd.read_sql_query("SELECT * FROM outcomes", con)
        arms = pd.read_sql_query("SELECT * FROM learner_arms", con)
        audit = pd.read_sql_query("SELECT * FROM audit_log ORDER BY id DESC LIMIT 300", con)
    finally:
        con.close()
    return events, decisions, actions, outcomes, arms, audit


def sequential_bg(value, vmin=0, vmax=100):
    """A single-hue sequential ramp, computed (not eyeballed) between the palette's
    lightest and darkest blue steps, so recovery/success rates read as a heatmap."""
    if pd.isna(value):
        return ""
    t = max(0.0, min(1.0, (value - vmin) / (vmax - vmin))) if vmax > vmin else 0.0
    c1 = tuple(int(SEQUENTIAL_RAMP[0][i : i + 2], 16) for i in (1, 3, 5))
    c2 = tuple(int(SEQUENTIAL_RAMP[1][i : i + 2], 16) for i in (1, 3, 5))
    rgb = tuple(round(c1[j] + (c2[j] - c1[j]) * t) for j in range(3))
    text = "#ffffff" if t > 0.55 else "#0b0b0b"
    return f"background-color: rgb({rgb[0]},{rgb[1]},{rgb[2]}); color: {text}"


def status_bg(value):
    color = STATUS_COLORS.get(value, "#c3c2b7")
    return f"background-color: {color}22; color: {color}; font-weight: 600;"


def bar_chart(counts: pd.Series, color_map: dict, label_map: dict):
    keys = list(counts.index)
    labels = [label_map.get(k, k) for k in keys]
    colors = [color_map.get(k, "#c3c2b7") for k in keys]
    fig = go.Figure(
        go.Bar(
            x=labels,
            y=counts.values,
            marker_color=colors,
            text=counts.values,
            textposition="outside",
            width=0.5,
        )
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=20, b=10, l=10, r=10),
        height=290,
        showlegend=False,
        font=dict(family=FONT_STACK, color="#52514e"),
        xaxis=dict(showgrid=False, tickfont=dict(color="#52514e", size=13)),
        yaxis=dict(showgrid=True, gridcolor="#e1e0d9", zeroline=False, tickfont=dict(color="#898781")),
        bargap=0.4,
    )
    return fig


st.title("AI Revenue Recovery Agent")
st.caption("Live batch view - Detector → Triage → Strategist → Critic → Guardrail/Orchestrator → Executor → Learner")

try:
    events, decisions, actions, outcomes, arms, audit = load_tables()
except Exception as exc:
    st.error(f"Could not read the database (it may be mid-write, this refreshes automatically): {exc}")
    st.stop()

if events.empty:
    st.info("No events yet. Once the Detector picks something up, it'll show here.")
    st.stop()

# ---- Headline hero figure ----
total_at_risk = events["amount"].fillna(0).sum()
total_recovered = outcomes.loc[outcomes["outcome"].isin(["recovered", "unattributed_recovery"]), "amount_recovered"].fillna(0).sum()
recovery_rate = (total_recovered / total_at_risk * 100) if total_at_risk else 0
attributed_recovered = outcomes.loc[outcomes["outcome"] == "recovered", "amount_recovered"].fillna(0).sum()
unattributed_recovered = outcomes.loc[outcomes["outcome"] == "unattributed_recovery", "amount_recovered"].fillna(0).sum()

resolved_mask = events["status"].isin(TERMINAL_STATUSES)
in_flight = (~resolved_mask).sum()
resolved_count = int(resolved_mask.sum())

st.markdown(
    f"""
<div class="hero-card">
  <div class="hero-label">Total recovered</div>
  <div class="hero-figure">₹{total_recovered / 100:,.0f}</div>
  <div class="hero-sub">{recovery_rate:.1f}% of ₹{total_at_risk / 100:,.0f} at risk recovered</div>
</div>
""",
    unsafe_allow_html=True,
)

tiles = [
    ("Total at risk", f"₹{total_at_risk / 100:,.0f}", f"{len(events)} events tracked"),
    ("Attributed recovery", f"₹{attributed_recovered / 100:,.0f}", "credited to a specific intervention"),
    ("Unattributed (late) recovery", f"₹{unattributed_recovered / 100:,.0f}", "paid after we'd already closed the case"),
    ("Events in flight", f"{int(in_flight)}", f"{resolved_count} of {len(events)} resolved"),
]
cols = st.columns(4)
for col, (label, value, delta) in zip(cols, tiles):
    col.markdown(
        f"""<div class="stat-tile"><div class="label">{label}</div>
        <div class="value">{value}</div><div class="delta">{delta}</div></div>""",
        unsafe_allow_html=True,
    )

st.divider()

# ---- Event status / channel breakdown ----
left, right = st.columns(2)
with left:
    st.markdown('<div class="section-title">Events by status</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="section-caption">Color = state (recovered/escalated/unresolved use the reserved status palette)</div>',
        unsafe_allow_html=True,
    )
    status_counts = events["status"].value_counts()
    st.plotly_chart(bar_chart(status_counts, STATUS_COLORS, STATUS_LABELS), width="stretch", config={"displayModeBar": False})
with right:
    st.markdown('<div class="section-title">Events by channel</div>', unsafe_allow_html=True)
    st.markdown('<div class="section-caption">Color = channel identity (fixed categorical order)</div>', unsafe_allow_html=True)
    type_counts = events["event_type"].value_counts()
    st.plotly_chart(bar_chart(type_counts, CHANNEL_COLORS, CHANNEL_LABELS), width="stretch", config={"displayModeBar": False})

st.divider()

# ---- Recovery rate by failure reason / intervention type ----
st.markdown('<div class="section-title">Recovery rate by failure reason and intervention type</div>', unsafe_allow_html=True)
if not decisions.empty:
    merged = decisions.merge(events[["id", "failure_reason", "amount", "event_type"]], left_on="event_id", right_on="id", suffixes=("", "_event"))
    merged = merged.merge(outcomes[["decision_id", "outcome"]], left_on="id", right_on="decision_id", how="left")
    approved = merged[merged["guardrail_verdict"] == "approved"].copy()
    approved["failure_reason"] = approved["failure_reason"].fillna("not_applicable")
    approved["recovered"] = approved["outcome"] == "recovered"

    breakdown = (
        approved.groupby(["failure_reason", "intervention_type"])
        .agg(attempts=("id", "count"), recovered=("recovered", "sum"))
        .reset_index()
    )
    breakdown["recovery_rate_pct"] = (breakdown["recovered"] / breakdown["attempts"] * 100).round(1)
    breakdown = breakdown.sort_values("attempts", ascending=False).rename(
        columns={
            "failure_reason": "Failure reason",
            "intervention_type": "Intervention",
            "attempts": "Attempts",
            "recovered": "Recovered",
            "recovery_rate_pct": "Recovery rate %",
        }
    )
    styled = breakdown.style.map(sequential_bg, subset=["Recovery rate %"]).format({"Recovery rate %": "{:.1f}"})
    st.dataframe(styled, width="stretch", hide_index=True)
else:
    st.caption("No approved decisions yet.")

st.divider()

# ---- Learner bandit table ----
st.markdown('<div class="section-title">Learner: success-rate table</div>', unsafe_allow_html=True)
st.markdown('<div class="section-caption">The strategy-shift evidence - how the bandit\'s per-arm success rate moves as the batch runs</div>', unsafe_allow_html=True)
if not arms.empty:
    arms = arms.copy()
    arms["total"] = arms["successes"] + arms["failures"]
    arms["success_rate_pct"] = (arms["successes"] / arms["total"] * 100).round(1)
    arms_display = arms.sort_values("total", ascending=False)[
        ["failure_reason", "intervention_type", "amount_bracket", "successes", "failures", "success_rate_pct", "updated_at"]
    ].rename(
        columns={
            "failure_reason": "Failure reason",
            "intervention_type": "Intervention",
            "amount_bracket": "Amount bracket",
            "successes": "Successes",
            "failures": "Failures",
            "success_rate_pct": "Success rate %",
            "updated_at": "Last updated",
        }
    )
    styled_arms = arms_display.style.map(sequential_bg, subset=["Success rate %"]).format({"Success rate %": "{:.1f}"})
    st.dataframe(styled_arms, width="stretch", hide_index=True)
else:
    st.caption("No bandit updates yet - arms populate once attempts start resolving (success or failure).")

st.divider()

# ---- Exception list ----
st.markdown('<div class="section-title">Exception list</div>', unsafe_allow_html=True)
st.markdown('<div class="section-caption">Shown honestly, not hidden - every case the system couldn\'t resolve on its own</div>', unsafe_allow_html=True)
exceptions = events[events["status"].isin(["escalated", "unresolved"])].copy()
if not exceptions.empty:
    exceptions_display = exceptions[
        ["id", "event_type", "razorpay_entity_id", "customer_id", "failure_reason", "amount", "status", "root_cause_category"]
    ].copy()
    exceptions_display["amount"] = (exceptions_display["amount"].fillna(0) / 100).round(2)
    exceptions_display["event_type"] = exceptions_display["event_type"].map(CHANNEL_LABELS).fillna(exceptions_display["event_type"])
    exceptions_display = exceptions_display.rename(
        columns={
            "id": "ID",
            "event_type": "Channel",
            "razorpay_entity_id": "Razorpay entity",
            "customer_id": "Customer",
            "failure_reason": "Failure reason",
            "amount": "Amount (₹)",
            "status": "Status",
            "root_cause_category": "Root cause",
        }
    )
    styled_exceptions = exceptions_display.style.map(status_bg, subset=["Status"]).format({"Amount (₹)": "{:,.2f}"})
    st.dataframe(styled_exceptions, width="stretch", hide_index=True)
else:
    st.caption("No exceptions yet.")

st.divider()

# ---- Audit trail ----
st.markdown('<div class="section-title">Audit trail</div>', unsafe_allow_html=True)
st.markdown('<div class="section-caption">Most recent 300 entries - every agent\'s reasoning, in plain English</div>', unsafe_allow_html=True)
if not audit.empty:
    audit_display = audit[["created_at", "actor", "action", "event_id", "detail"]].rename(
        columns={"created_at": "Time", "actor": "Agent", "action": "Action", "event_id": "Event ID", "detail": "Detail"}
    )
    st.dataframe(audit_display, width="stretch", hide_index=True, height=400)
else:
    st.caption("No audit entries yet.")

st.caption("Auto-refreshes every few seconds. Amounts in INR (converted from paise).")

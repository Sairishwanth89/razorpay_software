import html
import json
import sqlite3
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.config import settings

st.set_page_config(page_title="Recovery Mesh", layout="wide", page_icon="🔷")

DB_PATH = settings.database_url.split("///")[-1]

TERMINAL_STATUSES = ("recovered", "escalated", "unresolved")
MONO_STACK = "ui-monospace, 'SF Mono', 'Cascadia Code', 'Consolas', 'Menlo', monospace"
SANS_STACK = "system-ui, -apple-system, 'Segoe UI', sans-serif"

# ---- Status is reserved and mode-invariant (dataviz skill: "never themed") - same
# hex in both light and dark. ----
STATUS_COLORS = {
    "recovered": "#0ca30c", "escalated": "#fab219", "unresolved": "#ec835a", "superseded": "#8a8a84",
    "open": "#5598e7", "triaged": "#3987e5", "decided": "#2a78d6", "executed": "#1c5cab",
}
STATUS_LABELS = {
    "recovered": "Recovered", "escalated": "Escalated", "unresolved": "Unresolved",
    "superseded": "Superseded", "open": "Open", "triaged": "Triaged",
    "decided": "Decided", "executed": "Executed",
}
CHANNEL_LABELS = {
    "payment_failed": "Payment Retry", "checkout_abandoned": "Checkout Recovery",
    "invoice_overdue": "Receivables Negotiator", "subscription_halted": "Mandate Agent",
}
CHANNEL_AGENT_NAMES = {
    "Payment Retry Agent": "payment_failed", "Checkout Recovery Agent": "checkout_abandoned",
    "Receivables Negotiator Agent": "invoice_overdue", "Mandate Agent": "subscription_halted",
}

# ---- Two committed looks (not an automatic OS-detected flip - an explicit in-app
# toggle) sharing the same validated categorical order, each re-stepped for its own
# surface per the dataviz skill's snap-to-passing method. ----
THEMES = {
    "dark": {
        "page_bg": "#0d0d0d",
        "ink_primary": "#ffffff", "ink_secondary": "#c3c2b7", "ink_muted": "#8a8a84",
        "surface": "#141413", "border": "rgba(255,255,255,0.10)", "accent": "#3987e5", "gridline": "#2c2c2a",
        "channel": {"payment_failed": "#3987e5", "checkout_abandoned": "#d95926", "invoice_overdue": "#199e70", "subscription_halted": "#c98500"},
        "agent_dots": {"detector": "#199e70", "triage": "#c98500", "strategist": "#3987e5", "critic": "#9085e9",
                        "guardrail": "#3fbf3f", "orchestrator": "#d95926", "executor": "#d55181", "learner": "#8a8a84",
                        "scheduler": "#8a8a84", "poller": "#8a8a84"},
        "sequential": ("#184f95", "#86b6ef"),  # dim -> bright reads as "more" on a dark surface
    },
    "light": {
        "page_bg": "#f9f9f7",
        "ink_primary": "#0b0b0b", "ink_secondary": "#52514e", "ink_muted": "#767570",
        "surface": "#fcfcfb", "border": "rgba(11,11,11,0.10)", "accent": "#2a78d6", "gridline": "#e1e0d9",
        "channel": {"payment_failed": "#2a78d6", "checkout_abandoned": "#eb6834", "invoice_overdue": "#1baf7a", "subscription_halted": "#eda100"},
        "agent_dots": {"detector": "#1baf7a", "triage": "#eda100", "strategist": "#2a78d6", "critic": "#4a3aa7",
                        "guardrail": "#008300", "orchestrator": "#eb6834", "executor": "#e87ba4", "learner": "#767570",
                        "scheduler": "#767570", "poller": "#767570"},
        "sequential": ("#cde2fb", "#0d366b"),  # light -> dark reads as "more" on a light surface
    },
}

if "theme_mode" not in st.session_state:
    st.session_state.theme_mode = "dark"

header_l, header_r = st.columns([6, 1])
with header_r:
    picked = st.radio("Theme", options=["Dark", "Light"], horizontal=True, label_visibility="collapsed",
                       index=0 if st.session_state.theme_mode == "dark" else 1, key="theme_radio")
    st.session_state.theme_mode = picked.lower()

T = THEMES[st.session_state.theme_mode]

st.markdown(
    f"""
<style>
html, body, [class*="css"] {{ font-family: {SANS_STACK}; }}
/* Streamlit's own root chrome is set once at server start via .streamlit/config.toml
   (base="dark") - it does NOT react to this in-app toggle on its own, so the root
   containers need an explicit override here or light mode leaves dark text on a dark
   background outside our custom cards. */
[data-testid="stAppViewContainer"], [data-testid="stHeader"], [data-testid="stMain"], .stApp, body {{
  background-color: {T["page_bg"]} !important;
}}
.block-container {{ padding-top: 3.2rem; max-width: 1440px; }}
div[data-testid="stRadio"] label p {{ font-family: {MONO_STACK}; font-size: 13px; color: {T["ink_secondary"]} !important; }}

.brand-row {{ display: flex; align-items: center; gap: 12px; margin-bottom: 4px; }}
.brand-mark {{ width: 16px; height: 16px; background: {T["accent"]}; transform: rotate(45deg); border-radius: 3px; flex-shrink: 0; }}
.brand-name {{ font-size: 30px; font-weight: 700; letter-spacing: 0.03em; color: {T["ink_primary"]}; line-height: 1.2; }}
.brand-caption {{ font-family: {MONO_STACK}; font-size: 13.5px; color: {T["ink_muted"]}; margin-bottom: 20px; line-height: 1.6; }}

.hero-card {{ background: {T["surface"]}; border: 1px solid {T["border"]}; border-radius: 10px; padding: 24px 28px; margin-bottom: 16px; }}
.hero-label {{ font-family: {MONO_STACK}; font-size: 13px; letter-spacing: 0.06em; text-transform: uppercase; color: {T["ink_muted"]}; margin-bottom: 8px; }}
.hero-figure {{ font-size: 56px; font-weight: 700; color: {T["ink_primary"]}; line-height: 1.1; }}
.hero-sub {{ font-size: 15px; color: #0ca30c; margin-top: 8px; font-weight: 600; }}

.stat-tile {{ background: {T["surface"]}; border: 1px solid {T["border"]}; border-radius: 8px; padding: 16px 18px; height: 100%; }}
.stat-tile .label {{ font-family: {MONO_STACK}; font-size: 12px; letter-spacing: 0.05em; text-transform: uppercase; color: {T["ink_muted"]}; margin-bottom: 7px; }}
.stat-tile .value {{ font-size: 28px; font-weight: 700; color: {T["ink_primary"]}; }}
.stat-tile .delta {{ font-family: {MONO_STACK}; font-size: 13px; color: {T["ink_secondary"]}; margin-top: 5px; }}

.section-title {{ font-size: 18px; font-weight: 700; color: {T["ink_primary"]}; margin: 8px 0 3px 0; letter-spacing: 0.01em; }}
.section-caption {{ font-family: {MONO_STACK}; font-size: 13px; color: {T["ink_muted"]}; margin-bottom: 14px; }}

.attention-banner {{
  display: flex; align-items: center; gap: 10px; background: #fab21918; border: 1px solid #fab21955;
  border-radius: 8px; padding: 11px 16px; margin-bottom: 16px; font-family: {MONO_STACK}; font-size: 13.5px; color: {T["ink_primary"]};
}}
.attention-dot {{ width: 8px; height: 8px; border-radius: 50%; background: #fab219; flex-shrink: 0; }}
.attention-banner b {{ color: #fab219; }}
.agent-strip {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 20px; }}
.agent-card {{ background: {T["surface"]}; border: 1px solid {T["border"]}; border-radius: 8px; padding: 14px 16px; }}
.agent-card .top {{ display: flex; align-items: center; gap: 9px; margin-bottom: 7px; }}
.agent-dot {{ width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }}
.agent-dot.live {{ animation: pulse 1.8s ease-in-out infinite; }}
@keyframes pulse {{
  0%   {{ box-shadow: 0 0 0 0 rgba(60,220,140,0.55); }}
  70%  {{ box-shadow: 0 0 0 7px rgba(60,220,140,0); }}
  100% {{ box-shadow: 0 0 0 0 rgba(60,220,140,0); }}
}}
.agent-name {{ font-family: {MONO_STACK}; font-size: 14px; color: {T["ink_primary"]}; font-weight: 600; }}
.agent-stat {{ font-family: {MONO_STACK}; font-size: 12.5px; color: {T["ink_muted"]}; }}
.agent-stat b {{ color: {T["ink_secondary"]}; }}

.console {{
  background: {T["surface"]}; border: 1px solid {T["border"]}; border-radius: 8px;
  padding: 10px 4px; max-height: 460px; overflow-y: auto; font-family: {MONO_STACK}; font-size: 13.5px;
}}
.console-row {{ display: grid; grid-template-columns: 82px 20px 220px 200px 1fr; gap: 10px; padding: 5px 14px; border-radius: 4px; align-items: baseline; }}
.console-row:hover {{ background: rgba(128,128,128,0.08); }}
.console-time {{ color: {T["ink_muted"]}; }}
.console-dot-wrap {{ display: flex; align-items: center; height: 100%; }}
.console-dot {{ width: 7px; height: 7px; border-radius: 2px; flex-shrink: 0; }}
.console-agent {{ color: {T["ink_secondary"]}; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.console-action {{ color: {T["ink_primary"]}; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.console-detail {{ color: {T["ink_secondary"]}; overflow-wrap: anywhere; }}
.console-empty {{ color: {T["ink_muted"]}; padding: 16px; }}
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


def sequential_bg(value, ramp, vmin=0, vmax=100):
    if pd.isna(value):
        return ""
    t = max(0.0, min(1.0, (value - vmin) / (vmax - vmin))) if vmax > vmin else 0.0
    c1 = tuple(int(ramp[0][i : i + 2], 16) for i in (1, 3, 5))
    c2 = tuple(int(ramp[1][i : i + 2], 16) for i in (1, 3, 5))
    rgb = tuple(round(c1[j] + (c2[j] - c1[j]) * t) for j in range(3))
    lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
    text = "#0b0b0b" if lum > 140 else "#ffffff"
    return f"background-color: rgb({rgb[0]},{rgb[1]},{rgb[2]}); color: {text}"


def status_bg(value):
    color = STATUS_COLORS.get(value, "#8a8a84")
    return f"background-color: {color}22; color: {color}; font-weight: 600;"


def narrate_detail(detail_str: str) -> str:
    if not detail_str or detail_str == "{}":
        return "—"
    try:
        parsed = json.loads(detail_str)
    except (TypeError, ValueError):
        return str(detail_str)
    if not isinstance(parsed, dict):
        return str(parsed)
    for key in ("rationale", "reasoning", "reason", "issue"):
        if parsed.get(key):
            return str(parsed[key])
    parts = [f"{k}: {v}" for k, v in parsed.items() if v not in (None, "")]
    return "; ".join(parts) if parts else "—"


def bar_chart(counts: pd.Series, color_map: dict, label_map: dict, theme: dict):
    keys = list(counts.index)
    labels = [label_map.get(k, k) for k in keys]
    colors = [color_map.get(k, "#8a8a84") for k in keys]
    fig = go.Figure(
        go.Bar(x=labels, y=counts.values, marker_color=colors, text=counts.values, textposition="outside", width=0.5)
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=24, b=10, l=10, r=10),
        height=290,
        showlegend=False,
        font=dict(family=SANS_STACK, color=theme["ink_secondary"], size=13),
        xaxis=dict(showgrid=False, tickfont=dict(color=theme["ink_secondary"], size=14)),
        yaxis=dict(showgrid=True, gridcolor=theme["gridline"], zeroline=False, tickfont=dict(color=theme["ink_muted"], size=13)),
        bargap=0.4,
    )
    return fig


st.markdown(
    '<div class="brand-row"><div class="brand-mark"></div><span class="brand-name">RECOVERY MESH</span></div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="brand-caption">detector · triage · strategist · critic · guardrail/orchestrator · executor · learner — concurrent channel agents, live</div>',
    unsafe_allow_html=True,
)

try:
    events, decisions, actions, outcomes, arms, audit = load_tables()
except Exception as exc:
    st.error(f"Could not read the database (it may be mid-write, this refreshes automatically): {exc}")
    st.stop()

if events.empty:
    st.info("No events yet. Once the Detector picks something up, it'll show here.")
    st.stop()

# ---- Agent status strip ----
audit_recent = audit.copy()
audit_recent["created_at_parsed"] = pd.to_datetime(audit_recent["created_at"], errors="coerce")
recent_cutoff = pd.Timestamp.now() - pd.Timedelta(seconds=30)

strip_html = ['<div class="agent-strip">']
for channel_key, label in CHANNEL_LABELS.items():
    channel_events = events[events["event_type"] == channel_key]
    in_flight_n = (~channel_events["status"].isin(TERMINAL_STATUSES)).sum()
    agent_name = next(n for n, k in CHANNEL_AGENT_NAMES.items() if k == channel_key)
    recent = audit_recent[(audit_recent["actor"] == agent_name) & (audit_recent["created_at_parsed"] > recent_cutoff)]
    live = len(recent) > 0
    dot_class = "agent-dot live" if live else "agent-dot"
    dot_color = T["channel"][channel_key] if live else "#5a5a56"
    status_word = "active" if live else "idle"
    strip_html.append(
        f'<div class="agent-card"><div class="top">'
        f'<div class="{dot_class}" style="background:{dot_color}"></div>'
        f'<div class="agent-name">{label.upper()}</div></div>'
        f'<div class="agent-stat"><b>{len(channel_events)}</b> handled · <b>{int(in_flight_n)}</b> in flight · {status_word}</div>'
        f"</div>"
    )
strip_html.append("</div>")
st.markdown("".join(strip_html), unsafe_allow_html=True)

# ---- Human-in-the-loop attention banner ----
escalated_events = events[events["status"] == "escalated"]
if not escalated_events.empty:
    escalated_value = escalated_events["amount"].fillna(0).sum()
    st.markdown(
        f'<div class="attention-banner"><div class="attention-dot"></div>'
        f'<div><b>{len(escalated_events)} case{"s" if len(escalated_events) != 1 else ""} need human review</b> — '
        f'guardrail bounds exhausted, ₹{escalated_value / 100:,.0f} at risk sitting with a human decision. '
        f'See the exception list below.</div></div>',
        unsafe_allow_html=True,
    )

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
    st.markdown('<div class="section-caption">status palette — reserved, never themed</div>', unsafe_allow_html=True)
    status_counts = events["status"].value_counts()
    st.plotly_chart(bar_chart(status_counts, STATUS_COLORS, STATUS_LABELS, T), width="stretch", config={"displayModeBar": False})
with right:
    st.markdown('<div class="section-title">Events by channel</div>', unsafe_allow_html=True)
    st.markdown('<div class="section-caption">categorical palette — fixed slot order</div>', unsafe_allow_html=True)
    type_counts = events["event_type"].value_counts()
    st.plotly_chart(bar_chart(type_counts, T["channel"], CHANNEL_LABELS, T), width="stretch", config={"displayModeBar": False})

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
            "failure_reason": "Failure reason", "intervention_type": "Intervention",
            "attempts": "Attempts", "recovered": "Recovered", "recovery_rate_pct": "Recovery rate %",
        }
    )
    styled = breakdown.style.map(lambda v: sequential_bg(v, T["sequential"]), subset=["Recovery rate %"]).format({"Recovery rate %": "{:.1f}"})
    st.dataframe(styled, width="stretch", hide_index=True)
else:
    st.caption("No approved decisions yet.")

st.divider()

# ---- Learner bandit table ----
st.markdown('<div class="section-title">Learner: success-rate table</div>', unsafe_allow_html=True)
st.markdown('<div class="section-caption">the strategy-shift evidence</div>', unsafe_allow_html=True)
if not arms.empty:
    arms = arms.copy()
    arms["total"] = arms["successes"] + arms["failures"]
    arms["success_rate_pct"] = (arms["successes"] / arms["total"] * 100).round(1)
    arms_display = arms.sort_values("total", ascending=False)[
        ["failure_reason", "intervention_type", "amount_bracket", "successes", "failures", "success_rate_pct", "updated_at"]
    ].rename(
        columns={
            "failure_reason": "Failure reason", "intervention_type": "Intervention", "amount_bracket": "Amount bracket",
            "successes": "Successes", "failures": "Failures", "success_rate_pct": "Success rate %", "updated_at": "Last updated",
        }
    )
    styled_arms = arms_display.style.map(lambda v: sequential_bg(v, T["sequential"]), subset=["Success rate %"]).format({"Success rate %": "{:.1f}"})
    st.dataframe(styled_arms, width="stretch", hide_index=True)
else:
    st.caption("No bandit updates yet - arms populate once attempts start resolving (success or failure).")

st.divider()

# ---- Exception list ----
st.markdown('<div class="section-title">Exception list</div>', unsafe_allow_html=True)
st.markdown('<div class="section-caption">shown honestly, not hidden</div>', unsafe_allow_html=True)
exceptions = events[events["status"].isin(["escalated", "unresolved"])].copy()
if not exceptions.empty:
    exceptions_display = exceptions[
        ["id", "event_type", "razorpay_entity_id", "customer_id", "failure_reason", "amount", "status", "root_cause_category"]
    ].copy()
    exceptions_display["amount"] = (exceptions_display["amount"].fillna(0) / 100).round(2)
    exceptions_display["event_type"] = exceptions_display["event_type"].map(CHANNEL_LABELS).fillna(exceptions_display["event_type"])
    for col in ("customer_id", "failure_reason", "root_cause_category"):
        exceptions_display[col] = exceptions_display[col].fillna("—")
    exceptions_display = exceptions_display.rename(
        columns={
            "id": "ID", "event_type": "Channel", "razorpay_entity_id": "Razorpay entity", "customer_id": "Customer",
            "failure_reason": "Failure reason", "amount": "Amount (₹)", "status": "Status", "root_cause_category": "Root cause",
        }
    )
    styled_exceptions = exceptions_display.style.map(status_bg, subset=["Status"]).format({"Amount (₹)": "{:,.2f}"})
    st.dataframe(styled_exceptions, width="stretch", hide_index=True)
else:
    st.caption("No exceptions yet.")

st.divider()

# ---- Audit trail: console log ----
st.markdown('<div class="section-title">Agent log</div>', unsafe_allow_html=True)
st.markdown('<div class="section-caption">most recent 300 · every agent\'s reasoning, live</div>', unsafe_allow_html=True)
if not audit.empty:
    event_ids = sorted(audit["event_id"].dropna().unique().tolist())
    picked_event = st.selectbox(
        "Follow one event end to end",
        options=["All events"] + [int(e) for e in event_ids],
    )

    log_rows = audit.copy()
    if picked_event != "All events":
        log_rows = log_rows[log_rows["event_id"] == picked_event]

    lines = ['<div class="console">']
    if log_rows.empty:
        lines.append('<div class="console-empty">no entries</div>')
    for _, row in log_rows.iterrows():
        time_str = str(row["created_at"])[11:19] if len(str(row["created_at"])) >= 19 else str(row["created_at"])
        actor = html.escape(str(row["actor"]))
        dot_color = T["agent_dots"].get(row["actor"]) or T["channel"].get(CHANNEL_AGENT_NAMES.get(row["actor"]), "#8a8a84")
        eid = "—" if pd.isna(row["event_id"]) else str(int(row["event_id"]))
        action_text = html.escape(str(row["action"]))
        detail_text = html.escape(narrate_detail(row["detail"]))
        lines.append(
            '<div class="console-row">'
            f'<div class="console-time">{html.escape(time_str)}</div>'
            f'<div class="console-dot-wrap"><div class="console-dot" style="background:{dot_color}"></div></div>'
            f'<div class="console-agent" title="{actor}">{actor}</div>'
            f'<div class="console-action" title="{action_text}">{action_text} · #{eid}</div>'
            f'<div class="console-detail">{detail_text}</div>'
            "</div>"
        )
    lines.append("</div>")
    st.markdown("".join(lines), unsafe_allow_html=True)
else:
    st.caption("No audit entries yet.")

st.caption("Auto-refreshes every few seconds · amounts in INR (converted from paise)")

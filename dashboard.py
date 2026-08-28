import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from app.config import settings

st.set_page_config(page_title="AI Revenue Recovery Agent", layout="wide")

DB_PATH = settings.database_url.split("///")[-1]

TERMINAL_STATUSES = ("recovered", "escalated", "unresolved")


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


st.title("AI Revenue Recovery Agent")
st.caption("Live batch view - Detector → Triage → Strategist → Guardrail → Executor → Learner")

try:
    events, decisions, actions, outcomes, arms, audit = load_tables()
except Exception as exc:
    st.error(f"Could not read the database (it may be mid-write, this refreshes automatically): {exc}")
    st.stop()

if events.empty:
    st.info("No events yet. Once the Detector picks something up, it'll show here.")
    st.stop()

# ---- Headline metrics ----
total_at_risk = events["amount"].fillna(0).sum()
total_recovered = outcomes.loc[outcomes["outcome"].isin(["recovered", "unattributed_recovery"]), "amount_recovered"].fillna(0).sum()
recovery_rate = (total_recovered / total_at_risk * 100) if total_at_risk else 0
attributed_recovered = outcomes.loc[outcomes["outcome"] == "recovered", "amount_recovered"].fillna(0).sum()
unattributed_recovered = outcomes.loc[outcomes["outcome"] == "unattributed_recovery", "amount_recovered"].fillna(0).sum()

resolved_mask = events["status"].isin(TERMINAL_STATUSES)
in_flight = (~resolved_mask).sum()

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Total at risk", f"Rs {total_at_risk / 100:,.0f}")
col2.metric("Total recovered", f"Rs {total_recovered / 100:,.0f}", f"{recovery_rate:.1f}% recovery rate")
col3.metric("Attributed recovery", f"Rs {attributed_recovered / 100:,.0f}")
col4.metric("Unattributed (late) recovery", f"Rs {unattributed_recovered / 100:,.0f}")
col5.metric("Events in flight", int(in_flight), f"{len(events)} total")

st.divider()

# ---- Event status breakdown ----
left, right = st.columns([1, 1])
with left:
    st.subheader("Events by status")
    status_counts = events["status"].value_counts()
    st.bar_chart(status_counts)
with right:
    st.subheader("Events by type")
    type_counts = events["event_type"].value_counts()
    st.bar_chart(type_counts)

st.divider()

# ---- Recovery rate by failure reason / intervention type ----
st.subheader("Recovery rate by failure reason and intervention type")
if not decisions.empty:
    merged = decisions.merge(events[["id", "failure_reason", "amount", "event_type"]], left_on="event_id", right_on="id", suffixes=("", "_event"))
    merged = merged.merge(
        outcomes[["decision_id", "outcome"]], left_on="id", right_on="decision_id", how="left"
    )
    approved = merged[merged["guardrail_verdict"] == "approved"].copy()
    approved["failure_reason"] = approved["failure_reason"].fillna("not_applicable")
    approved["recovered"] = approved["outcome"] == "recovered"

    breakdown = (
        approved.groupby(["failure_reason", "intervention_type"])
        .agg(attempts=("id", "count"), recovered=("recovered", "sum"))
        .reset_index()
    )
    breakdown["recovery_rate_pct"] = (breakdown["recovered"] / breakdown["attempts"] * 100).round(1)
    st.dataframe(breakdown.sort_values("attempts", ascending=False), use_container_width=True, hide_index=True)
else:
    st.caption("No approved decisions yet.")

st.divider()

# ---- Learner bandit table ----
st.subheader("Learner: success-rate table (the strategy-shift evidence)")
if not arms.empty:
    arms = arms.copy()
    arms["total"] = arms["successes"] + arms["failures"]
    arms["success_rate_pct"] = (arms["successes"] / arms["total"] * 100).round(1)
    st.dataframe(
        arms.sort_values("total", ascending=False)[
            ["failure_reason", "intervention_type", "amount_bracket", "successes", "failures", "success_rate_pct", "updated_at"]
        ],
        use_container_width=True,
        hide_index=True,
    )
else:
    st.caption("No bandit updates yet - arms populate once attempts start resolving (success or failure).")

st.divider()

# ---- Exception list ----
st.subheader("Exception list (shown honestly, not hidden)")
exceptions = events[events["status"].isin(["escalated", "unresolved"])].copy()
if not exceptions.empty:
    exceptions_display = exceptions[
        ["id", "event_type", "razorpay_entity_id", "customer_id", "failure_reason", "amount", "status", "root_cause_category"]
    ].copy()
    exceptions_display["amount"] = (exceptions_display["amount"].fillna(0) / 100).round(2)
    st.dataframe(exceptions_display, use_container_width=True, hide_index=True)
else:
    st.caption("No exceptions yet.")

st.divider()

# ---- Audit trail ----
st.subheader("Audit trail (most recent 300)")
if not audit.empty:
    audit_display = audit[["created_at", "actor", "action", "event_id", "detail"]].copy()
    st.dataframe(audit_display, use_container_width=True, hide_index=True, height=400)
else:
    st.caption("No audit entries yet.")

st.caption("Auto-refreshes every few seconds. Amounts in INR (converted from paise).")

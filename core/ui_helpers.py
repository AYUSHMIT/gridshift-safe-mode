# core/ui_helpers.py
"""
[Owned by: System Security teammate / UI]

Small helpers for app.py. Pure data shaping for tables + chart, plus
one cached briefing accessor. Kept here so app.py stays focused on the
UI flow rather than row layout.
"""
from __future__ import annotations
import altair as alt
import pandas as pd
import streamlit as st
from core.ai_narrator import IncidentNarrator, Briefing
from core.state import TickResult


# --------------------------------------------------------------------------
# DataFrame shaping
# --------------------------------------------------------------------------

def build_trust_df(latest: TickResult) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "node": a.node_id,
            "trust": a.level.value,
            "sig": "✔" if a.verification.signature_ok else "✘",
            "pcr": "✔" if a.verification.pcr_ok else "✘",
            "nonce": "✔" if a.verification.nonce_ok else "✘",
            "reported (MW)": round(a.reported_load_mw, 2),
            "observed (MW)": round(a.observed_load_mw, 2),
            "mismatch (MW)": round(a.mismatch_mw, 2),
        }
        for a in latest.assessments
    ])


def build_decisions_df(latest: TickResult) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "job": d.job_id,
            "action": d.action.value,
            "source": d.source_dc,
            "target": d.target_dc or "-",
            "reason": d.reason,
        }
        for d in latest.decisions
    ])


def build_fleet_df(orch) -> pd.DataFrame:
    rows = []
    for dc_id, dc in orch.fleet.dcs.items():
        util_pct = (
            dc.observed_load_mw() / dc.capacity_mw * 100
            if dc.capacity_mw else 0
        )
        rows.append({
            "dc": dc_id,
            "region": dc.region,
            "running jobs": len(dc.running_jobs),
            "delayed jobs": len(dc.delayed_jobs),
            "true load (MW)": round(dc.true_load_mw(), 2),
            "reported (MW)": round(dc.reported_load_mw(), 2),
            "util %": f"{util_pct:.0f}",
            "lying": "⚠ yes" if dc.lying else "no",
            "spike (MW)": round(dc.spike_mw, 2),
        })
    return pd.DataFrame(rows)


def detect_active_attacks(orch) -> list:
    """
    Inspect orchestrator state and return a list of (node_id, description)
    tuples for every active attack. Pure function -- no Streamlit calls.

    The "active attack" set is the union of three independent sensors:
      - DC.lying flag                  (behavioral attack injected via UI)
      - DC.spike_mw > 0                (load-spike attack injected via UI)
      - Prover firmware self-check     (firmware tamper attack)

    Asks each prover whether its own firmware still matches the known-good
    baseline rather than reaching into PCR internals; the prover owns
    that comparison.
    """
    items = []
    for dc_id, dc in orch.fleet.dcs.items():
        if dc.lying:
            items.append((
                dc_id,
                f"behavioral lie (under-reports by {dc.lie_delta_mw:.0f} MW)",
            ))
        if dc.spike_mw > 0:
            items.append((
                dc_id,
                f"real load spike (+{dc.spike_mw:.0f} MW)",
            ))
    for dc_id, prover in orch.provers.items():
        if not prover.firmware_matches_known_good():
            items.append((dc_id, "firmware tampered (PCR mismatch)"))
    return items


# --------------------------------------------------------------------------
# Altair chart
# --------------------------------------------------------------------------

def build_load_history_chart(history: list) -> alt.Chart:
    """Total Boston load over time, with threshold line and safe-mode bands."""
    hist_df = pd.DataFrame([
        {
            "tick": t.tick,
            "total_mw": t.grid.total_load_mw,
            "threshold": t.grid.threshold_mw,
            "safe_mode": t.safe_mode,
        }
        for t in history
    ])

    base = alt.Chart(hist_df).encode(x=alt.X("tick:Q", title="Tick"))

    load_line = base.mark_line(strokeWidth=3, color="#1f77b4").encode(
        y=alt.Y(
            "total_mw:Q",
            title="Total Boston grid load (MW)",
            scale=alt.Scale(zero=False),
        ),
        tooltip=["tick", "total_mw", "threshold", "safe_mode"],
    )

    threshold_line = base.mark_rule(
        strokeWidth=3, color="red", strokeDash=[6, 4]
    ).encode(y="threshold:Q")

    threshold_label = base.mark_text(
        align="right", baseline="bottom", dx=-5, dy=-3,
        color="red", fontWeight="bold", fontSize=12,
    ).encode(
        x=alt.X("tick:Q", aggregate="max"),
        y=alt.Y("threshold:Q", aggregate="max"),
        text=alt.value("⚠ 900 MW safety threshold"),
    )

    ribbon_rows = [
        {"tick_start": t.tick - 0.5, "tick_end": t.tick + 0.5}
        for t in history if t.safe_mode
    ]
    if ribbon_rows:
        ribbon_chart = alt.Chart(pd.DataFrame(ribbon_rows)).mark_rect(
            opacity=0.10, color="red",
        ).encode(x="tick_start:Q", x2="tick_end:Q")
        chart = (
            ribbon_chart + load_line + threshold_line + threshold_label
        ).properties(height=320)
    else:
        chart = (
            load_line + threshold_line + threshold_label
        ).properties(height=320)

    return chart


# --------------------------------------------------------------------------
# AI briefing cache
# --------------------------------------------------------------------------
#
# Streamlit re-runs the whole script on every interaction, so we keep
# the narrator instance and a per-tick briefing cache in session_state.
# The narrator is created ONCE in app.py's top-of-file init block; this
# helper assumes it exists and just toggles the prefer_llm flag.

def get_briefing(latest: TickResult, use_ai: bool, regenerate: bool) -> Briefing:
    cache_key = (latest.tick, use_ai)
    if regenerate or cache_key not in st.session_state.briefings:
        st.session_state.narrator.prefer_llm = use_ai
        with st.spinner("Generating operator briefing..."):
            briefing = st.session_state.narrator.narrate(latest)
        st.session_state.briefings[cache_key] = briefing
    return st.session_state.briefings[cache_key]


def briefing_source_badge(briefing: Briefing) -> str:
    """Human-friendly label for the briefing's origin, used as caption text."""
    if briefing.source == "anthropic":
        return f"🟣 Anthropic `{briefing.model}`"
    if briefing.source == "openai":
        return f"🟢 OpenAI `{briefing.model}`"
    return "⚙️ rule-based fallback (offline)"
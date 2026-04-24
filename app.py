# app.py
"""
GridShift - Streamlit dashboard.

Run with:
    streamlit run app.py
"""
import streamlit as st
import pandas as pd
from core.orchestrator import GridShiftOrchestrator


st.set_page_config(page_title="GridShift", layout="wide")

if "orch" not in st.session_state:
    st.session_state.orch = GridShiftOrchestrator()
    st.session_state.history = []

orch = st.session_state.orch

st.title("⚡ GridShift — Grid-Aware, Trust-Verified AI Orchestration")
st.caption(
    "Attestation + behavioral consistency. "
    "Safe mode UNWINDS workloads off untrusted nodes; it does not freeze them."
)

# -------- Controls --------
st.subheader("Simulation controls")
c1, c2, c3, c4 = st.columns(4)
if c1.button("▶ Tick"):
    st.session_state.history.append(orch.tick())
if c2.button("⏩ Tick x5"):
    for _ in range(5):
        st.session_state.history.append(orch.tick())
if c3.button("🌡 Heatwave"):
    orch.trigger_heatwave(60)
if c4.button("📦 Job burst"):
    orch.submit_job_burst(14)

st.subheader("Attack scenarios")
a1, a2, a3, a4 = st.columns(4)
if a1.button("🕵 Lie (behavioral)"):
    orch.start_attack_lying("BOS-1", 16.0)
if a2.button("🔧 Tamper (firmware)"):
    orch.start_attack_tamper("BOS-1")
if a3.button("💥 Load spike"):
    # Supervisor scenario step 2: inflate real load after attestation fails
    orch.spike_load("BOS-1", 25.0)
if a4.button("🧹 Clear attacks"):
    orch.clear_attacks()

st.divider()

# -------- Latest state --------
if not st.session_state.history:
    st.info("Click **Tick** to advance the simulation.")
else:
    latest = st.session_state.history[-1]

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Tick", latest.tick)
    m2.metric("Total load (MW)", f"{latest.grid.total_load_mw:.1f}")
    m3.metric("Threshold (MW)", f"{latest.grid.threshold_mw:.0f}")
    m4.metric("Overload risk", f"{latest.grid.overload_risk*100:.1f}%")
    m5.metric("Safe mode", "🔴 ON" if latest.safe_mode else "🟢 OFF")

    st.subheader("Per-node trust")
    rows = [{
        "node": a.node_id,
        "trust": a.level.value,
        "sig": "✔" if a.verification.signature_ok else "✘",
        "pcr": "✔" if a.verification.pcr_ok else "✘",
        "nonce": "✔" if a.verification.nonce_ok else "✘",
        "reported (MW)": round(a.reported_load_mw, 2),
        "observed (MW)": round(a.observed_load_mw, 2),
        "mismatch (MW)": round(a.mismatch_mw, 2),
        "reason": a.reason,
    } for a in latest.assessments]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    st.subheader("Decisions this tick")
    if latest.decisions:
        st.dataframe(pd.DataFrame([{
            "job": d.job_id,
            "action": d.action.value,
            "source": d.source_dc,
            "target": d.target_dc or "-",
            "reason": d.reason,
        } for d in latest.decisions]), width="stretch", hide_index=True)
    else:
        st.success("No actions needed this tick.")

    st.subheader("Load history")
    hist = pd.DataFrame([{
        "tick": t.tick,
        "total_mw": t.grid.total_load_mw,
        "threshold": t.grid.threshold_mw,
    } for t in st.session_state.history])
    st.line_chart(hist.set_index("tick"))

    st.subheader("Fleet state")
    fleet_rows = []
    for dc_id, dc in orch.fleet.dcs.items():
        util_pct = (dc.observed_load_mw() / dc.capacity_mw * 100
                    if dc.capacity_mw else 0)
        fleet_rows.append({
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
    st.dataframe(pd.DataFrame(fleet_rows), width="stretch", hide_index=True)

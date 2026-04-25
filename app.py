# app.py
"""
GridShift - Streamlit dashboard.

Run with:
    streamlit run app.py
"""
import streamlit as st
import pandas as pd
from core.orchestrator import GridShiftOrchestrator
from core.state import TrustLevel
from core.ai_narrator import IncidentNarrator
from core.ui_helpers import (
    build_trust_df,
    build_decisions_df,
    build_fleet_df,
    build_load_history_chart,
    get_briefing,
    briefing_source_badge,
)


# ---------- Setup ----------

st.set_page_config(page_title="GridShift", layout="wide")


def fresh_orchestrator():
    return GridShiftOrchestrator()


if "orch" not in st.session_state:
    st.session_state.orch = fresh_orchestrator()
    st.session_state.history = []
    st.session_state.demo_step = 0
    st.session_state.last_step_label = "(not started)"
    st.session_state.narrator = IncidentNarrator()
    st.session_state.briefings = {}  # tick_num -> Briefing
    st.session_state.use_ai = True

orch = st.session_state.orch

# ---------- Header ----------

st.title("⚡ GridShift — Grid-Aware, Trust-Verified AI Orchestration")
st.caption(
    "**AI-driven grid optimization + cryptographic trust verification.** "
    "Deterministic safety layer decides; an LLM-powered narrator briefs the operator. "
    "Safe mode unwinds workloads off untrusted nodes — it does not freeze them."
)

# Invariant banner
st.info(
    "**Invariants**  •  Never place new work onto an untrusted node.  "
    "•  If observed load exceeds the safety limit, unwind non-critical work — "
    "regardless of what the controller reports."
)

# ---------- Demo stepper (suggestion #1, #7) ----------

DEMO_STEPS = [
    ("Step 1: Heatwave begins",
     "Boston enters heatwave; baseline grid load rises.",
     lambda o: o.trigger_heatwave(60)),
    ("Step 2: AI job burst arrives",
     "A wave of AI jobs lands across the three data centers.",
     lambda o: o.submit_job_burst(14)),
    ("Step 3: Behavioral lie attack on BOS-1",
     "BOS-1 controller starts under-reporting its real load by ~16 MW.",
     lambda o: o.start_attack_lying("BOS-1", 16.0)),
    ("Step 4: Firmware tamper attack on BOS-1",
     "Attacker swaps the BOS-1 firmware; PCR no longer matches known-good.",
     lambda o: (o.stop_attack_lying("BOS-1"), o.start_attack_tamper("BOS-1"))),
    ("Step 5: Load spike + unwind",
     "Attacker inflates real load on the (already untrusted) BOS-1. "
     "Watch GridShift unwind workloads OFF BOS-1 instead of freezing.",
     lambda o: o.spike_load("BOS-1", 25.0)),
]


st.subheader("🎬 Guided demo")
sc1, sc2, sc3, sc4 = st.columns([2.2, 1, 1, 1])
sc1.markdown(f"**Last step run:** {st.session_state.last_step_label}")

if sc2.button("▶ Next demo step", type="primary"):
    if st.session_state.demo_step < len(DEMO_STEPS):
        label, _desc, fn = DEMO_STEPS[st.session_state.demo_step]
        fn(orch)
        st.session_state.history.append(orch.tick())
        st.session_state.last_step_label = label
        st.session_state.demo_step += 1

if sc3.button("🏆 Load winning scenario"):
    o = fresh_orchestrator()
    o.trigger_heatwave(60)
    o.submit_job_burst(14)
    o.tick(); o.tick()
    o.start_attack_tamper("BOS-1")
    o.spike_load("BOS-1", 25.0)
    h = [o.tick(), o.tick()]
    st.session_state.orch = o
    st.session_state.history = h
    st.session_state.demo_step = len(DEMO_STEPS)
    st.session_state.last_step_label = "🏆 Winning scenario (Scene 4)"
    orch = o

if sc4.button("🔄 Reset full demo"):
    st.session_state.orch = fresh_orchestrator()
    st.session_state.history = []
    st.session_state.demo_step = 0
    st.session_state.last_step_label = "(not started)"
    orch = st.session_state.orch

# Show next-step hint
if st.session_state.demo_step < len(DEMO_STEPS):
    next_label, next_desc, _ = DEMO_STEPS[st.session_state.demo_step]
    st.caption(f"**Next:** {next_label} — {next_desc}")
else:
    st.caption("Demo complete. Use **Reset full demo** to start over.")

with st.expander("Manual controls (advanced)"):
    mc1, mc2, mc3, mc4 = st.columns(4)
    if mc1.button("Tick"):
        st.session_state.history.append(orch.tick())
    if mc2.button("Tick x5"):
        for _ in range(5):
            st.session_state.history.append(orch.tick())
    if mc3.button("Heatwave"):
        orch.trigger_heatwave(60)
    if mc4.button("Job burst"):
        orch.submit_job_burst(14)

    ma1, ma2, ma3, ma4 = st.columns(4)
    if ma1.button("Lie (behavioral)"):
        orch.start_attack_lying("BOS-1", 16.0)
    if ma2.button("Tamper (firmware)"):
        orch.start_attack_tamper("BOS-1")
    if ma3.button("Load spike"):
        orch.spike_load("BOS-1", 25.0)
    if ma4.button("Clear attacks"):
        orch.clear_attacks()

st.divider()

# ---------- If no history yet, show a primer and stop ----------
if not st.session_state.history:
    st.info("Click **▶ Next demo step** to begin the guided demo.")
    st.stop()

latest = st.session_state.history[-1]
bos1 = next((a for a in latest.assessments if a.node_id == "BOS-1"), None)


# ---------- Active attack panel (suggestion #5) ----------

def detect_active_attacks(orch):
    """Inspect orchestrator state to describe what attacks are live."""
    items = []
    for dc_id, dc in orch.fleet.dcs.items():
        if dc.lying:
            items.append((dc_id, f"behavioral lie (under-reports by {dc.lie_delta_mw:.0f} MW)"))
        if dc.spike_mw > 0:
            items.append((dc_id, f"real load spike (+{dc.spike_mw:.0f} MW)"))
    # Tamper detection: ask each prover whether its own firmware still
    # matches the known-good baseline. Keeps the UI free of attestation
    # internals -- the prover owns its own self-check.
    for dc_id, prover in orch.provers.items():
        if not prover.firmware_matches_known_good():
            items.append((dc_id, "firmware tampered (PCR mismatch)"))
    return items


active_attacks = detect_active_attacks(orch)
if active_attacks:
    by_node = {}
    for node, desc in active_attacks:
        by_node.setdefault(node, []).append(desc)
    lines = []
    for node, descs in by_node.items():
        lines.append(f"**{node}** — " + "; ".join(descs))
    st.error("🚨 **Active attack**\n\n" + "\n\n".join(lines))
else:
    st.success("✅ No active attacks. System operating under normal trust.")


# ---------- Top metrics ----------

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Tick", latest.tick)
m2.metric("Total grid load (MW)", f"{latest.grid.total_load_mw:.1f}",
          delta=f"{latest.grid.total_load_mw - latest.grid.threshold_mw:+.1f} vs threshold")
m3.metric("Threshold (MW)", f"{latest.grid.threshold_mw:.0f}")
m4.metric("Overload risk", f"{latest.grid.overload_risk*100:.1f}%")
m5.metric("Safe mode", "🔴 ON" if latest.safe_mode else "🟢 OFF")


# ---------- Reported vs Observed (suggestion: highlight the heart of the story) ----------

if bos1 is not None:
    st.subheader("🔬 Reported vs Observed — the heart of the story")
    r1, r2, r3, r4 = st.columns(4)
    r1.metric(f"Reported {bos1.node_id} (MW)", f"{bos1.reported_load_mw:.1f}")
    r2.metric(f"Observed {bos1.node_id} (MW)", f"{bos1.observed_load_mw:.1f}")
    r3.metric("Mismatch (MW)", f"{bos1.mismatch_mw:.1f}",
              delta=f"{'EXCEEDS' if bos1.mismatch_mw > 10 else 'within'} 10 MW limit",
              delta_color=("inverse" if bos1.mismatch_mw > 10 else "normal"))
    r4.metric("Behavioral threshold (MW)", "10.0")


# ---------- Load history with bold threshold line (suggestion: visually scream) ----------

st.subheader("📈 Load history")
st.altair_chart(
    build_load_history_chart(st.session_state.history),
    use_container_width=True,
)
st.caption(
    "🔵 Total Boston grid load   "
    "🟥 dashed line = 900 MW safety threshold   "
    "🟥 shaded bands = safe mode active"
)


# ---------- Trust legend (suggestion #4) ----------

with st.expander("ℹ️ How to read the trust columns"):
    st.markdown(
        "- **sig** — controller's identity is cryptographically valid "
        "(signature verifies against the registered public key)\n"
        "- **pcr** — controller's firmware hash matches the known-good value "
        "(no firmware tampering)\n"
        "- **nonce** — packet echoes a fresh, unused nonce we just issued "
        "(replay protection)\n"
        "- **mismatch (MW)** — gap between what the controller reports and "
        "what the grid-side sensor observes; > 10 MW means the controller is lying\n"
        "- **trust** — overall: TRUSTED only if all four checks pass"
    )


# ---------- Per-node trust table (suggestion #8: keep only what matters) ----------

st.subheader("🔐 Per-node trust")
st.dataframe(build_trust_df(latest), width="stretch", hide_index=True)


# ---------- Decisions table (suggestion #8) ----------

st.subheader("🧭 Decisions this tick")
if latest.decisions:
    st.dataframe(build_decisions_df(latest), width="stretch", hide_index=True)
else:
    if latest.safe_mode:
        st.warning(
            "🟡 **Safe mode remains active**, but no additional unwind action is "
            "needed this tick because observed load is currently below the "
            "75% per-DC safety limit and the grid total is below 900 MW."
        )
    else:
        st.success("No actions needed this tick — system is stable and trusted.")


# ---------- Why this happened (suggestion #2) ----------

def build_narration(latest, orch):
    """Produce a plain-English explanation of the tick."""
    lines = []
    bad = [a for a in latest.assessments if a.level != TrustLevel.TRUSTED]
    if bad:
        for a in bad:
            reasons = []
            if not a.verification.pcr_ok:
                reasons.append("PCR mismatch (firmware tampered)")
            if not a.verification.signature_ok:
                reasons.append("invalid signature")
            if not a.verification.nonce_ok:
                reasons.append("stale/replayed nonce")
            if a.mismatch_mw > 10:
                reasons.append(
                    f"reported vs observed load diverged by "
                    f"{a.mismatch_mw:.1f} MW"
                )
            why = " + ".join(reasons) if reasons else "borderline anomaly"
            lines.append(
                f"**{a.node_id}** is **{a.level.value.upper()}** "
                f"because {why}."
            )
        nodes = ", ".join(a.node_id for a in bad)
        lines.append(
            f"GridShift **blocks migrations INTO** {nodes} but **permits "
            f"and prefers migration OUT** to reduce exposure."
        )
    else:
        lines.append("All controllers attest cleanly and report consistent load.")

    # Explain each migration decision
    for d in latest.decisions:
        if d.action.value == "migrate":
            lines.append(
                f"**{d.job_id}** migrated **{d.source_dc} → {d.target_dc}** "
                f"because {d.reason}"
            )
        elif d.action.value == "block":
            lines.append(
                f"**{d.job_id}** migration blocked: {d.reason}"
            )
        elif d.action.value == "delay":
            lines.append(
                f"**{d.job_id}** delayed: {d.reason}"
            )
    return lines


st.subheader("💡 Why this happened")
for line in build_narration(latest, orch):
    st.markdown(f"- {line}")


# ---------- AI Incident Briefing (LLM-generated) ----------

st.subheader("🤖 AI-generated incident briefing")
st.caption(
    "The deterministic safety layer decides; this panel is an LLM "
    "observer that turns the structured tick state into an operator "
    "briefing. If no API key is configured or the network is unavailable, "
    "a rule-based fallback runs automatically."
)

ai_c1, ai_c2 = st.columns([4, 1])
with ai_c2:
    st.session_state.use_ai = st.toggle(
        "Enable AI narrator",
        value=st.session_state.use_ai,
        help="Turn off to see the rule-based fallback only.",
    )
    regenerate = st.button("🔄 Regenerate briefing")

briefing = get_briefing(latest, st.session_state.use_ai, regenerate)

with ai_c1:
    st.caption(
        f"Source: {briefing_source_badge(briefing)}  •  {briefing.latency_ms} ms"
    )
    # Render the briefing in a styled "operator log" box
    st.markdown(
        "<div style='padding:14px 18px; background:#F8F9FA; "
        "border-left:5px solid #1F4E79; border-radius:4px; "
        "font-family: Georgia, serif; font-size:15px; line-height:1.6;'>"
        f"{briefing.text}"
        "</div>",
        unsafe_allow_html=True,
    )


# ---------- Directional safe-mode indicator (suggestion #6) ----------

bad_nodes = [a.node_id for a in latest.assessments
             if a.level != TrustLevel.TRUSTED]
if bad_nodes:
    st.subheader("🛡️ Safe mode is directional")
    d1, d2 = st.columns(2)
    nodes_str = ", ".join(bad_nodes)
    d1.error(f"❌ **Blocked**\n\nMigrations **INTO** {nodes_str}")
    d2.success(f"✅ **Allowed / preferred**\n\nMigrations **OUT OF** {nodes_str}")


# ---------- Old policy vs GridShift (suggestion #3) ----------

with st.expander("🆚 Naive safe mode vs GridShift directional unwind"):
    st.table(pd.DataFrame([
        {"Policy": "Naive safe mode",
         "Behavior": "freeze compromised node",
         "Result": "❌ attacker traps load"},
        {"Policy": "GridShift",
         "Behavior": "unwind from compromised node",
         "Result": "✅ load moves off unsafe node"},
    ]))


# ---------- Fleet state ----------

st.subheader("🏢 Fleet state")
st.dataframe(build_fleet_df(orch), width="stretch", hide_index=True)


# ---------- Final takeaway (suggestion #10) ----------

st.divider()
st.markdown(
    "<div style='text-align:center; padding:18px; "
    "background:#EAF2F8; border-left:6px solid #1F4E79; "
    "border-radius:6px; font-size:18px;'>"
    "<b>Takeaway:</b> GridShift optimizes when trust holds, "
    "and safely unwinds when trust breaks."
    "</div>",
    unsafe_allow_html=True,
)
# GridShift — Safe-Mode Orchestration for Grid-Aware AI Workloads

A grid-aware AI orchestrator that refuses to act on telemetry it cannot trust.

## The idea in one line

Every data-center controller **cryptographically attests** to its own integrity and **signs** every telemetry packet. The orchestrator cross-checks two independent signals — cryptographic attestation *and* behavioral consistency (reported vs. observed load). If either fails, the system enters **safe mode** — which UNWINDS workloads off the untrusted node rather than freezing them in place. An **LLM-powered incident narrator** turns each tick's structured state into a plain-language operator briefing.

## Where the AI lives

GridShift's AI component is honest about what it does and does not do:

- **The AI does not make control decisions.** All decisions (run / delay / migrate / block) are made by a deterministic safety layer. Putting an LLM in the control loop of a power grid would be reckless; we don't do that.
- **The AI generates operator briefings.** After each tick, the structured trust state and decision list are passed to an LLM (Claude or GPT, configurable) which produces a 3–5 sentence operator-log-style briefing: what happened, what GridShift did, and what the operator should inspect.
- **The AI falls back cleanly offline.** If no API key is configured or the network is down, a rule-based fallback generates an operator briefing deterministically. The demo works with or without wifi.

This separation — *AI for explanation, not decision* — is itself part of the pitch. It's the right architectural pattern for AI in critical infrastructure.

## Design refinement — supervisor feedback (April 2026)

An early version of the design blocked *all* migrations involving an untrusted node. A hardware-security supervisor pointed out this could be weaponized: an adversary with a workload on a DC could trigger a false attestation failure, and then inflate the load on that DC, using the migration block to trap their own inflated workload in place and create a DoS against the grid. The refined design below addresses this directly.

**Refined safety policy:**

- **Migration INTO** an untrusted node → **blocked** (never place new work on a dubious node).
- **Migration OUT OF** an untrusted node → **allowed and preferred** (reduce exposure; unwind).
- **Grid-side observed load** is always authoritative for hard safety limits, independent of trust state. If observed utilization on any node exceeds 75%, an unwind-migration is emitted regardless of what the controller reports.
- Safe mode is an **investigate-and-unwind** state, not a freeze.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

### Optional: enable the LLM narrator

Without an API key, the AI panel runs a deterministic rule-based fallback. To use an actual LLM:

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # preferred
# or
export OPENAI_API_KEY=sk-...
streamlit run app.py
```

See `.env.example` for configurable model IDs. At a venue with unreliable wifi, force offline mode with `export GRIDSHIFT_FORCE_FALLBACK=1`.

## Running the core loop without the UI

```bash
python -m core.orchestrator
```

This runs the supervisor-scenario attack end-to-end and prints the unwind behavior.

## Module tests

```bash
python -m core.grid_model
python -m core.dc_simulator
python -m core.verifier
```

## Demo scenes (on the dashboard)

The dashboard now has a **🎬 Guided demo** with a single `▶ Next demo step` button that walks through five steps, plus a `🏆 Load winning scenario` shortcut for time-pressured demos. Manual controls are tucked into an expander.

1. **Step 1 — Heatwave begins.** Baseline grid load rises.
2. **Step 2 — AI job burst.** A wave of AI jobs lands across the three DCs.
3. **Step 3 — Behavioral lie.** BOS-1 under-reports by 16 MW. Behavioral monitor catches the mismatch; trust flips to compromised; safe mode ON.
4. **Step 4 — Firmware tamper.** PCR no longer matches known-good. Attestation catches it even when reported and observed agree.
5. **Step 5 — Load spike + unwind (the winning moment).** BOS-1 is untrusted AND its real load is inflated. The refined safety layer **migrates jobs OFF BOS-1** instead of freezing them — the supervisor-scenario DoS is defeated.

### UI features for judges

- **Active attack panel** — narrates exactly what is being injected at each moment.
- **Reported vs Observed metrics** — the heart of the story shown as a 4-up metric row.
- **Load history chart** — the 900 MW threshold is a bold red dashed line; safe-mode periods are shaded.
- **Trust legend** — explains what `sig`, `pcr`, `nonce`, and mismatch mean.
- **"Why this happened" box** — plain-English narration of every decision.
- **Directional safe mode** — explicit "Blocked: INTO BOS-1 / Allowed: OUT OF BOS-1" panel.
- **Naive vs GridShift comparison table** — shows the value of the directional unwind in one glance.
- **Reset full demo** and **Load winning scenario** buttons — recovery if the demo goes sideways.
- **Takeaway banner** — *GridShift optimizes when trust holds, and safely unwinds when trust breaks.*

## Core invariants (refined)

```
ACCEPT  ⟺  signature_valid ∧ pcr_matches_known_good ∧ nonce_fresh
TRUST   ⟺  ACCEPT ∧ |reported − observed| < ε   (per-node)
DECIDE  ⟺  planner, filtered by directional trust policy
          + observed-load override for hard safety limits
```

## Repo map

```
gridshift/
├── app.py                      # Streamlit UI
├── core/
│   ├── state.py                # shared types [everyone]
│   ├── grid_model.py           # [Smart Grids]
│   ├── dc_simulator.py         # [Optical / DC]
│   ├── attestation.py          # [HW Security] crypto primitives
│   ├── prover.py               # [HW Security] controller side
│   ├── verifier.py             # [HW Security] orchestrator side
│   ├── behavior_monitor.py     # [System Security]
│   ├── safety.py               # [System Security] decision + directional safety
│   ├── orchestrator.py         # [System Security] main tick loop
│   └── ai_narrator.py          # [System Security] LLM incident briefings
├── data/
│   └── sample_jobs.json
├── .env.example
├── requirements.txt
└── README.md
```

## Threat model coverage

| Attack                                                  | Behavioral check | Attestation check | Safety policy |
|---------------------------------------------------------|------------------|-------------------|---------------|
| Controller lies about load                              | ✔ caught         | passes            | block-into; unwind-out |
| Firmware tampered                                       | may pass         | ✔ caught          | block-into; unwind-out |
| Replay of a captured valid message                      | may pass         | ✔ caught (nonce)  | block-into; unwind-out |
| Stolen / spoofed controller identity                    | may pass         | ✔ caught (sig)    | block-into; unwind-out |
| **DoS via safe-mode weaponization** (supervisor scenario) | —              | attacker wants this | ✔ **defeated** — unwind + observed-load override |

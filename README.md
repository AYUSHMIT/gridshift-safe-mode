# GridShift — Safe-Mode Orchestration for Grid-Aware AI Workloads

A grid-aware AI orchestrator that refuses to act on telemetry it cannot trust.

## The idea in one line

Every data-center controller **cryptographically attests** to its own integrity and **signs** every telemetry packet. The orchestrator cross-checks two independent signals — cryptographic attestation *and* behavioral consistency (reported vs. observed load). If either fails, the system enters **safe mode**: migrations are blocked, flexible jobs are delayed, and critical jobs are preserved.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## Running the core loop without the UI

```bash
python -m core.orchestrator
```

This runs an end-to-end scenario (normal → behavioral attack → firmware attack) in the console.

## Module tests

Each module has a `__main__` smoke test:

```bash
python -m core.grid_model
python -m core.dc_simulator
python -m core.verifier
```

## Demo scenes (on the dashboard)

1. **Normal + heatwave** — click *Heatwave* → *Job burst* → *Tick x5*. Load crosses the threshold, the system migrates and delays jobs, load drops below threshold. Trust panel is all green.
2. **Behavioral attack** — click *Attack: lie* → *Tick*. Reported vs. observed diverges by 16 MW. Trust flips to *compromised*. Safe mode turns ON. Migrations become BLOCK. Critical jobs preserved.
3. **Firmware attack** — click *Clear attacks* → *Attack: tamper* → *Tick*. The PCR column goes ✘ even though reported and observed agree. This is the hardware-security story: tampered firmware is caught *before* any bad telemetry is trusted.

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
│   ├── safety.py               # [System Security] decision + safety
│   └── orchestrator.py         # [System Security] main tick loop
├── data/
│   └── sample_jobs.json
├── requirements.txt
└── README.md
```

## Core invariants

```
ACCEPT  ⟺  signature_valid ∧ pcr_matches_known_good ∧ nonce_fresh
TRUST   ⟺  ACCEPT ∧ |reported_load − observed_load| < ε
DECIDE  ⟺  if TRUST: full optimizer, else: safe mode
```

## Why this matters

As AI data centers push the grid toward its limits, operators will lean on AI agents to keep things stable. Those agents are only as trustworthy as their inputs — and adversaries know it. GridShift is a grid-aware AI controller that verifies its own telemetry and degrades safely when trust breaks down.

# GridShift — Safe-Mode Orchestration for Grid-Aware AI Workloads

A grid-aware AI orchestrator that refuses to act on telemetry it cannot trust.

## The idea in one line

Every data-center controller **cryptographically attests** to its own integrity and **signs** every telemetry packet. The orchestrator cross-checks two independent signals — cryptographic attestation *and* behavioral consistency (reported vs. observed load). If either fails, the system enters **safe mode** — which UNWINDS workloads off the untrusted node rather than freezing them in place.

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

1. **Normal + heatwave** — *Heatwave* → *Job burst* → *Tick x5*. Migrations and delays bring load back under threshold. All controllers trusted.
2. **Behavioral attack** — *Lie (behavioral)* → *Tick*. Reported-vs-observed diverges by 16 MW. Trust flips to compromised; safe mode ON; migrations INTO BOS-1 blocked.
3. **Firmware attack** — *Clear attacks* → *Tamper (firmware)* → *Tick*. PCR goes ✘ even though reported and observed agree. Safe mode ON before bad telemetry is ever used.
4. **Supervisor-scenario attack (DoS via safe mode)** — *Clear attacks* → *Tamper (firmware)* → *Load spike* → *Tick*. BOS-1 is untrusted AND its real load is inflated. The refined safety layer **actively migrates jobs OFF BOS-1** (observed-load override + unwind policy), defeating the attack.

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
│   └── orchestrator.py         # [System Security] main tick loop
├── data/
│   └── sample_jobs.json
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

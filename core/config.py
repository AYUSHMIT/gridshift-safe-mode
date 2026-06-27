# core/config.py
"""
[STEP 0 PROPOSAL — ratify together in the interface-lock meeting]

Single source of truth for every experiment knob. All four modules code
against this dataclass so the Monte-Carlo runner can sweep any parameter
without touching module internals.

Ownership of fields (who sets sensible defaults / justifies them):
  - topology, migration cost, workload .... Arash (DC)
  - noise_sigma_mw ........................ Mehran (grid sensing)
  - policy ................................ Ayush (security)
  - adversary block ....................... Ayush (behavioral) + Zahra (attestation)
  - run control ........................... shared / harness (Ayush)
"""
from dataclasses import dataclass, field
from typing import Tuple


@dataclass
class SimConfig:
    # ---- topology (Arash) ----
    regions: Tuple[str, ...] = ("Boston", "Worcester", "Springfield")
    dcs_per_region: int = 3                 # 3 regions x 3 = 9 DCs
    region_capacity_mw: float = 160.0       # per-region grid headroom (Mehran refines)

    # ---- workload (Arash) ----
    arrival_per_tick: float = 1.5           # mean new jobs/tick (Poisson); sweepable
    job_power_min_mw: float = 1.5
    job_power_max_mw: float = 6.0
    job_dur_min: int = 3
    job_dur_max: int = 12

    # ---- migration cost (Arash) ----
    migration_ticks: int = 2                # k: ticks a job is in-flight
    migration_overhead_mw: float = 1.0      # extra draw while in-flight
    sla_slack: float = 0.5                  # allowed lateness fraction before SLA breach

    # ---- grid sensing (Mehran) ----
    noise_sigma_mw: float = 0.0             # grid-side sensor noise std; Mehran sets default

    # ---- policy (Ayush) ----
    policy: str = "directional"             # "none" | "freeze" | "directional"

    # ---- adversary (Ayush behavioral / Zahra attestation) ----
    compromised_fraction: float = 0.0       # fraction of DCs adversary controls
    attack_start_tick: int = 10_000         # default: no attack
    lie_delta_mw: float = 0.0               # under-report magnitude
    stealthy: bool = False                  # lie just under detection threshold
    spike_mw: float = 0.0                   # injected real load (DoS step)
    firmware_tamper: bool = False           # PCR mismatch attack (Zahra)
    replay_nonce: bool = False              # stale-nonce attack (Zahra)

    # ---- run control (harness / Ayush) ----
    n_ticks: int = 200
    n_seeds: int = 30
    seed: int = 42

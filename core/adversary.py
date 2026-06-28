# core/adversary.py
"""
Experiment-facing adversary interface.

This module intentionally does not make safety decisions. It only injects
controlled faults into existing simulator/prover hooks so paper experiments can
sweep the threat model without reaching into orchestrator internals.
"""
import math
import random
from typing import TYPE_CHECKING

from core.config import SimConfig

if TYPE_CHECKING:
    from core.orchestrator import GridShiftOrchestrator


def _select_compromised_nodes(
    orch: "GridShiftOrchestrator",
    cfg: SimConfig,
) -> list[str]:
    """Select compromised nodes deterministically for reproducible runs."""
    if cfg.compromised_fraction <= 0.0:
        return []

    dc_ids = sorted(orch.fleet.dcs.keys())
    if not dc_ids:
        return []

    n = math.ceil(cfg.compromised_fraction * len(dc_ids))
    n = max(1, min(len(dc_ids), n))

    rng = random.Random(cfg.seed)
    return sorted(rng.sample(dc_ids, n))


def configure_adversary(
    orch: "GridShiftOrchestrator",
    cfg: SimConfig,
    tick: int,
) -> list[str]:
    """Apply configured attacks at the experiment attack tick.

    The default SimConfig is a no-op because attack_start_tick is far outside
    normal demo runs and compromised_fraction is zero. Experiments opt in by
    setting a finite attack_start_tick and compromised_fraction > 0.
    """
    if tick != cfg.attack_start_tick:
        return []

    compromised = _select_compromised_nodes(orch, cfg)
    if not compromised:
        return []

    events: list[str] = []
    for dc_id in compromised:
        if cfg.lie_delta_mw > 0.0:
            orch.fleet.enable_lie(dc_id, cfg.lie_delta_mw)
            events.append(
                f"tick {tick}: {dc_id} under-reports by "
                f"{cfg.lie_delta_mw:.1f} MW"
            )

        if cfg.spike_mw > 0.0:
            orch.fleet.spike(dc_id, cfg.spike_mw)
            events.append(
                f"tick {tick}: {dc_id} real load spike +{cfg.spike_mw:.1f} MW"
            )

        orch.provers[dc_id].configure_attacks(
            firmware_tamper=cfg.firmware_tamper,
            replay_nonce=cfg.replay_nonce,
            key_compromise=cfg.key_compromise,
        )
        active = []
        if cfg.firmware_tamper:
            active.append("firmware_tamper")
        if cfg.replay_nonce:
            active.append("replay_nonce")
        if cfg.key_compromise:
            active.append("key_compromise")
        if active:
            events.append(
                f"tick {tick}: {dc_id} attestation attack(s): "
                f"{','.join(active)}"
            )

    return events

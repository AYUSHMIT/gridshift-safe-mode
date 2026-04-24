# core/safety.py
"""
[Owned by: System Security teammate]

DecisionEngine:    picks candidate actions to keep the grid stable.
SafetyController:  filters those actions based on per-node trust levels.

Refined design (after feedback from the HW security supervisor):
  - Under COMPROMISED trust on a node, migrations *into* that node
    are blocked, but migrations *out of* it are ALLOWED and PREFERRED.
    This defeats the DoS-via-safe-mode attack: an adversary that
    triggers a false attestation failure on a DC and then inflates
    its load cannot trap those workloads in place.
  - The grid-side observed_load_mw is always authoritative for
    hard safety limits, independent of the reported/attested trust
    state. If observed load on any node exceeds a local overload
    threshold, an unwind-migration is emitted regardless.
  - Safe mode is an INVESTIGATE-AND-UNWIND state, not a freeze.
"""
from typing import List, Tuple, Dict
from core.state import (
    TrustLevel, Decision, ActionType, JobPriority, GridState
)
from core.dc_simulator import DataCenterFleet


# If a DC's true load exceeds this fraction of its capacity,
# emit an unwind-migration regardless of trust state.
LOCAL_UNWIND_UTILIZATION = 0.75


class DecisionEngine:
    def plan(
        self, grid: GridState, fleet: DataCenterFleet
    ) -> List[Decision]:
        decisions: List[Decision] = []
        projected = grid.total_load_mw
        if projected <= grid.threshold_mw:
            return decisions

        # Gather candidate jobs, biggest first
        candidates = []
        for dc in fleet.dcs.values():
            for j in dc.running_jobs:
                if j.priority != JobPriority.CRITICAL:
                    candidates.append((j, dc.dc_id))
        candidates.sort(key=lambda x: -x[0].power_mw)

        for job, src_dc in candidates:
            if projected <= grid.threshold_mw:
                break
            if job.priority == JobPriority.MIGRATABLE:
                target = self._find_migration_target(fleet, job, src_dc)
                if target is not None:
                    decisions.append(Decision(
                        job_id=job.job_id,
                        action=ActionType.MIGRATE,
                        source_dc=src_dc,
                        target_dc=target,
                        reason=(
                            f"GRID: reduce Boston load below "
                            f"{grid.threshold_mw:.0f} MW via geographic shift"
                        ),
                    ))
                    projected -= job.power_mw * 0.5
                    continue
            decisions.append(Decision(
                job_id=job.job_id,
                action=ActionType.DELAY,
                source_dc=src_dc,
                reason=(
                    f"GRID: delay non-critical job to bring load below "
                    f"{grid.threshold_mw:.0f} MW"
                ),
            ))
            projected -= job.power_mw
        return decisions

    def _find_migration_target(self, fleet, job, src_dc):
        src_region = fleet.dcs[src_dc].region
        for dc_id, dc in fleet.dcs.items():
            if dc.region != src_region and dc.can_accept(job):
                return dc_id
        return None


class SafetyController:
    """
    Safe mode is an INVESTIGATE-AND-UNWIND state:
      - migrations INTO a compromised node  -> blocked
      - migrations OUT OF a compromised node -> preferred
      - observed-load overrides always apply (grid-side sensor is
        authoritative for hard safety limits)
    """

    def apply(
        self,
        decisions: List[Decision],
        trust_by_node: Dict[str, TrustLevel],
        fleet: DataCenterFleet,
    ) -> Tuple[List[Decision], bool]:
        bad_nodes = {n for n, lvl in trust_by_node.items()
                     if lvl != TrustLevel.TRUSTED}
        safe_mode = len(bad_nodes) > 0

        filtered: List[Decision] = []

        # 1. Filter planned decisions by directional policy
        for d in decisions:
            # Migration TARGET untrusted -> block (never place new
            # work on a dubious node).
            if d.action == ActionType.MIGRATE and d.target_dc in bad_nodes:
                filtered.append(Decision(
                    job_id=d.job_id,
                    action=ActionType.BLOCK,
                    source_dc=d.source_dc,
                    target_dc=d.target_dc,
                    reason=(
                        f"SAFE MODE: refusing to migrate into "
                        f"untrusted node {d.target_dc}"
                    ),
                ))
                continue

            # Migration SOURCE untrusted -> preferred (unwind)
            if d.action == ActionType.MIGRATE and d.source_dc in bad_nodes:
                d.reason = (
                    f"SAFE MODE (unwind): preferred migration OUT of "
                    f"untrusted node {d.source_dc}"
                )
                filtered.append(d)
                continue

            # Delays are always fine
            if d.action == ActionType.DELAY:
                if safe_mode:
                    d.reason = f"[SAFE MODE] {d.reason}"
                filtered.append(d)
                continue

            if safe_mode:
                d.reason = f"[SAFE MODE] {d.reason}"
            filtered.append(d)

        # 2. Observed-load override: grid-side sensor is authoritative
        unwinds = self._observed_load_unwinds(fleet, bad_nodes)
        filtered.extend(unwinds)

        return filtered, safe_mode

    def _observed_load_unwinds(
        self, fleet: DataCenterFleet, bad_nodes: set
    ) -> List[Decision]:
        """
        For any DC whose observed utilization is above LOCAL_UNWIND_UTILIZATION,
        emit an unwind-migration of its largest non-critical job to any
        TRUSTED DC that can accept it. Fires even in safe mode and even
        for cleanly-attesting nodes -- the grid-side sensor is trusted.
        """
        unwinds: List[Decision] = []
        for src_id, dc in fleet.dcs.items():
            if dc.capacity_mw <= 0:
                continue
            util = dc.observed_load_mw() / dc.capacity_mw
            if util < LOCAL_UNWIND_UTILIZATION:
                continue

            hot_jobs = sorted(
                [j for j in dc.running_jobs
                 if j.priority != JobPriority.CRITICAL],
                key=lambda j: -j.power_mw,
            )
            for job in hot_jobs:
                target = None
                for tgt_id, tgt_dc in fleet.dcs.items():
                    if tgt_id == src_id:
                        continue
                    if tgt_id in bad_nodes:
                        continue   # never migrate INTO an untrusted node
                    if tgt_dc.can_accept(job):
                        target = tgt_id
                        break
                if target is not None:
                    tag = (
                        f"UNWIND: {src_id} observed utilization "
                        f"{util*100:.0f}% exceeds 75% safety limit "
                        f"-> migrate to {target}"
                    )
                    if src_id in bad_nodes:
                        tag = f"{tag} ({src_id} also untrusted; reduces exposure)"
                    unwinds.append(Decision(
                        job_id=job.job_id,
                        action=ActionType.MIGRATE,
                        source_dc=src_id,
                        target_dc=target,
                        reason=tag,
                    ))
                    # one unwind per DC per tick
                    break
        return unwinds

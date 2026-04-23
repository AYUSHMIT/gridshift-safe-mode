# core/safety.py
"""
[Owned by: System Security teammate]

DecisionEngine:    picks candidate actions (delay/migrate) to bring the
                   grid back under threshold. Priority-aware and greedy.

SafetyController:  filters those candidate actions based on the worst
                   trust level observed. Under COMPROMISED trust, any
                   high-impact action (migration) is BLOCKED.
"""
from typing import List, Tuple
from core.state import (
    TrustLevel, Decision, ActionType, JobPriority, GridState
)
from core.dc_simulator import DataCenterFleet


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
                        reason="reduce Boston load via geographic shift",
                    ))
                    # Migration reduces Boston-region load but still
                    # consumes power elsewhere; only partial relief to
                    # the Boston threshold.
                    projected -= job.power_mw * 0.5
                    continue
            decisions.append(Decision(
                job_id=job.job_id,
                action=ActionType.DELAY,
                source_dc=src_dc,
                reason="delay to relieve grid stress",
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
    Filters decisions based on the worst trust level across all nodes.
    This is the enforcement layer: under low trust, dangerous actions
    are downgraded to BLOCK. Critical jobs are always preserved.
    """
    def apply(
        self,
        decisions: List[Decision],
        trust_levels: List[TrustLevel],
    ) -> Tuple[List[Decision], bool]:
        worst = self._worst(trust_levels)
        if worst == TrustLevel.TRUSTED:
            return decisions, False

        filtered: List[Decision] = []
        for d in decisions:
            if worst == TrustLevel.COMPROMISED:
                # Block migrations entirely; allow delays only.
                if d.action == ActionType.MIGRATE:
                    filtered.append(Decision(
                        job_id=d.job_id,
                        action=ActionType.BLOCK,
                        source_dc=d.source_dc,
                        reason="SAFE MODE: migration blocked under compromised trust",
                    ))
                else:
                    filtered.append(d)
            else:  # SUSPICIOUS
                # Allow everything but stamp the reason.
                d.reason = f"[SUSPICIOUS TRUST] {d.reason}"
                filtered.append(d)
        return filtered, True

    def _worst(self, levels: List[TrustLevel]) -> TrustLevel:
        order = {
            TrustLevel.TRUSTED: 0,
            TrustLevel.SUSPICIOUS: 1,
            TrustLevel.COMPROMISED: 2,
        }
        if not levels:
            return TrustLevel.TRUSTED
        return max(levels, key=lambda l: order[l])

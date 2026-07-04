"""Shared experiment trial runner.

This module centralizes the per-tick simulation loop so paper experiments can
vary only the workload source, policy, detector mode, and attack settings.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.config import SimConfig
from core.orchestrator import GridShiftOrchestrator
from core.state import TrustLevel

from experiments.workloads import (
    DerivedTraceWorkloadSource,
    SyntheticWorkloadSource,
    TraceWorkloadSource,
)


@dataclass(frozen=True)
class TrialSpec:
    experiment: str
    case: str
    policy: str
    detector_mode: str
    seed: int
    attack_cfg: dict
    workload_source: object
    ticks: int


def _resolve_workload_jobs(workload_source, tick: int):
    if isinstance(
        workload_source,
        (SyntheticWorkloadSource, TraceWorkloadSource, DerivedTraceWorkloadSource),
    ):
        return workload_source.jobs_for_tick(tick)

    jobs_for_tick = getattr(workload_source, "jobs_for_tick", None)
    if callable(jobs_for_tick):
        return jobs_for_tick(tick)

    raise TypeError(f"Unsupported workload source: {type(workload_source)!r}")


def run_trial(*, spec: TrialSpec) -> dict:
    config_kwargs = {
        "seed": spec.seed,
        "policy": spec.policy,
        "detector_mode": spec.detector_mode,
    }
    config_kwargs.update(spec.attack_cfg)
    cfg = SimConfig(**config_kwargs)
    orch = GridShiftOrchestrator(seed=spec.seed, config=cfg)

    workload_source = spec.workload_source
    if hasattr(workload_source, "reset"):
        workload_source.reset(spec.seed)

    orch.trigger_heatwave(spec.ticks)

    overload_exceedance = 0.0
    safe_mode_ticks = 0
    bad_node_ticks = 0
    active_arrival_first_tick = None
    active_arrival_last_tick = None

    for tick in range(1, spec.ticks + 1):
        jobs = _resolve_workload_jobs(workload_source, tick)
        if jobs:
            active_arrival_first_tick = active_arrival_first_tick or tick
            active_arrival_last_tick = tick
            if isinstance(jobs, int):
                orch.submit_job_burst(jobs)
            else:
                orch.submit_jobs(jobs)

        result = orch.tick()
        overload_exceedance += max(
            0.0, result.grid.total_load_mw - result.grid.threshold_mw
        )
        if result.safe_mode:
            safe_mode_ticks += 1
        bad_node_ticks += sum(
            1 for assessment in result.assessments
            if assessment.level != TrustLevel.TRUSTED
        )

    submitted = orch.fleet._job_counter
    completed = len(orch.fleet.completed)
    sla = orch.fleet.sla_stats()
    total_submitted_work = _submitted_work(orch)

    return {
        "experiment": spec.experiment,
        "case": spec.case,
        "workload_source": getattr(spec.workload_source, "name", spec.case),
        "policy": spec.policy,
        "detector_mode": spec.detector_mode,
        "seed": spec.seed,
        "load_scale": float(getattr(spec.workload_source, "load_scale", 1.0)),
        "overload": float(overload_exceedance),
        "overload_exceedance": float(overload_exceedance),
        "safe_mode_ticks": float(safe_mode_ticks),
        "bad_node_ticks": float(bad_node_ticks),
        "migrations": float(orch.fleet.migration_count),
        "attack_start_tick": int(cfg.attack_start_tick),
        "active_arrival_first_tick": active_arrival_first_tick,
        "active_arrival_last_tick": active_arrival_last_tick,
        "submitted_jobs": int(submitted),
        "completed_jobs": int(completed),
        "total_submitted_work": float(total_submitted_work),
        "completion_rate": completed / max(1, submitted),
        "sla_violation_rate": float(sla["sla_violation_rate"]),
    }


def _submitted_work(orch: GridShiftOrchestrator) -> float:
    jobs = list(orch.fleet.completed) + list(orch.fleet.pending_jobs)
    for dc in orch.fleet.dcs.values():
        jobs.extend(dc.running_jobs)
        jobs.extend(dc.delayed_jobs)
    return sum(j.power_mw * j.base_duration_ticks for j in jobs)

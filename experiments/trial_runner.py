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
    SyntheticWorkloadSource,
    TraceWorkloadSource,
    jobs_from_trace_power,
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


def _resolve_workload_jobs(workload_source, tick: int, trace_points=None) -> int:
    if isinstance(workload_source, SyntheticWorkloadSource):
        return workload_source.jobs_for_tick(tick)

    if isinstance(workload_source, TraceWorkloadSource):
        points = trace_points if trace_points is not None else workload_source.load()
        power_mw = workload_source.power_for_tick(points, tick)
        return jobs_from_trace_power(power_mw)

    jobs_for_tick = getattr(workload_source, "jobs_for_tick", None)
    if callable(jobs_for_tick):
        return int(jobs_for_tick(tick))

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

    trace_points = None
    if isinstance(workload_source, TraceWorkloadSource):
        trace_points = workload_source.load()

    orch.trigger_heatwave(spec.ticks)

    overload_exceedance = 0.0
    safe_mode_ticks = 0
    bad_node_ticks = 0

    for tick in range(1, spec.ticks + 1):
        jobs = _resolve_workload_jobs(workload_source, tick, trace_points)
        if jobs:
            orch.submit_job_burst(jobs)

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

    return {
        "experiment": spec.experiment,
        "case": spec.case,
        "policy": spec.policy,
        "detector_mode": spec.detector_mode,
        "seed": spec.seed,
        "overload_exceedance": float(overload_exceedance),
        "safe_mode_ticks": float(safe_mode_ticks),
        "bad_node_ticks": float(bad_node_ticks),
        "migrations": float(orch.fleet.migration_count),
        "completion_rate": completed / max(1, submitted),
        "sla_violation_rate": float(sla["sla_violation_rate"]),
    }

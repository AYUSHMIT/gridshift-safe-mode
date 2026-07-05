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
    grid_config: object | None = None
    grid_trace_source: object | None = None
    apply_heatwave_to_trace: bool = False
    grid_metadata: dict | None = None
    config_overrides: dict | None = None


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
    if spec.config_overrides:
        config_kwargs.update(spec.config_overrides)
    cfg = SimConfig(**config_kwargs)
    orch = GridShiftOrchestrator(
        seed=spec.seed,
        config=cfg,
        grid_config=spec.grid_config,
        grid_trace_source=spec.grid_trace_source,
        apply_heatwave_to_trace=spec.apply_heatwave_to_trace,
    )

    workload_source = spec.workload_source
    if hasattr(workload_source, "reset"):
        workload_source.reset(spec.seed)

    orch.trigger_heatwave(spec.ticks)

    overload_exceedance = 0.0
    safe_mode_ticks = 0
    bad_node_ticks = 0
    migration_candidates_considered = 0
    candidates_with_trusted_feasible_destination = 0
    active_arrival_first_tick = None
    active_arrival_last_tick = None
    post_attack_results = []

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
        migration_candidates_considered += result.migration_candidates_considered
        candidates_with_trusted_feasible_destination += (
            result.candidates_with_trusted_feasible_destination
        )
        if tick >= int(cfg.attack_start_tick):
            post_attack_results.append(result)

    submitted = orch.fleet._job_counter
    completed = len(orch.fleet.completed)
    sla = orch.fleet.sla_stats()
    total_submitted_work = _submitted_work(orch)
    candidates_blocked_insufficient_destination_capacity = (
        migration_candidates_considered
        - candidates_with_trusted_feasible_destination
    )
    migration_feasibility_rate = (
        candidates_with_trusted_feasible_destination
        / migration_candidates_considered
        if migration_candidates_considered
        else 0.0
    )
    post_attack_summary = _post_attack_actuation_summary(
        post_attack_results,
        attack_start_tick=int(cfg.attack_start_tick),
    )

    row = {
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
        "migration_candidates_considered": int(migration_candidates_considered),
        "candidates_with_trusted_feasible_destination": int(
            candidates_with_trusted_feasible_destination
        ),
        "candidates_blocked_insufficient_destination_capacity": int(
            candidates_blocked_insufficient_destination_capacity
        ),
        "migration_feasibility_rate": float(migration_feasibility_rate),
        "experiment_ticks": int(spec.ticks),
        "attack_start_tick": int(cfg.attack_start_tick),
        "active_arrival_first_tick": active_arrival_first_tick,
        "active_arrival_last_tick": active_arrival_last_tick,
        "submitted_jobs": int(submitted),
        "completed_jobs": int(completed),
        "total_submitted_work": float(total_submitted_work),
        "completion_rate": completed / max(1, submitted),
        "sla_violation_rate": float(sla["sla_violation_rate"]),
    }
    row.update(post_attack_summary)
    row.update(_grid_metadata(orch, spec.grid_metadata))
    return row


def _post_attack_actuation_summary(results: list, *, attack_start_tick: int) -> dict:
    """Summarize post-attack TCAE observations.

    time_to_first_feasible_corrective_action uses -1 when no post-attack tick
    has a trusted compute-feasible destination for an untrusted-source job.
    """
    if not results:
        return {
            "post_attack_mean_migration_feasibility_rate": 0.0,
            "post_attack_fraction_ticks_with_any_feasible_trusted_destination": 0.0,
            "post_attack_mean_trusted_residual_headroom_mw": 0.0,
            "post_attack_min_trusted_residual_headroom_mw": 0.0,
            "post_attack_mean_trusted_destinations_with_positive_headroom": 0.0,
            "time_to_first_feasible_corrective_action": -1,
            "successful_corrective_migrations": 0,
            "post_attack_scheduler_migration_decisions_raw": 0,
            "post_attack_scheduler_migration_decisions_to_trusted_capacity_feasible": 0,
            "post_attack_safety_allowed_migrations": 0,
            "post_attack_safety_explicit_block_migrations": 0,
            "post_attack_safety_raw_migrations_removed_or_converted": 0,
            "post_attack_safety_blocked_migrations": 0,
            "post_attack_executed_migrations": 0,
            "post_attack_executed_corrective_migrations": 0,
        }

    first_feasible_tick = None
    for result in results:
        if result.candidates_with_trusted_feasible_destination > 0:
            first_feasible_tick = result.tick
            break

    return {
        "post_attack_mean_migration_feasibility_rate": _mean(
            result.migration_feasibility_rate for result in results
        ),
        "post_attack_fraction_ticks_with_any_feasible_trusted_destination": (
            sum(
                1 for result in results
                if result.candidates_with_trusted_feasible_destination > 0
            )
            / len(results)
        ),
        "post_attack_mean_trusted_residual_headroom_mw": _mean(
            result.trusted_residual_headroom_mw for result in results
        ),
        "post_attack_min_trusted_residual_headroom_mw": min(
            result.trusted_residual_headroom_mw for result in results
        ),
        "post_attack_mean_trusted_destinations_with_positive_headroom": _mean(
            result.trusted_destinations_with_positive_headroom
            for result in results
        ),
        "time_to_first_feasible_corrective_action": (
            -1
            if first_feasible_tick is None
            else int(first_feasible_tick) - int(attack_start_tick)
        ),
        "successful_corrective_migrations": sum(
            result.executed_corrective_migrations_this_tick
            for result in results
        ),
        "post_attack_scheduler_migration_decisions_raw": sum(
            result.scheduler_migration_decisions_raw for result in results
        ),
        "post_attack_scheduler_migration_decisions_to_trusted_capacity_feasible": sum(
            result.scheduler_migration_decisions_to_trusted_capacity_feasible
            for result in results
        ),
        "post_attack_safety_allowed_migrations": sum(
            result.safety_allowed_migrations for result in results
        ),
        "post_attack_safety_explicit_block_migrations": sum(
            result.safety_explicit_block_migrations for result in results
        ),
        "post_attack_safety_raw_migrations_removed_or_converted": sum(
            result.safety_raw_migrations_removed_or_converted
            for result in results
        ),
        "post_attack_safety_blocked_migrations": sum(
            result.safety_blocked_migrations for result in results
        ),
        "post_attack_executed_migrations": sum(
            result.executed_migrations_this_tick for result in results
        ),
        "post_attack_executed_corrective_migrations": sum(
            result.executed_corrective_migrations_this_tick
            for result in results
        ),
    }


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def _grid_metadata(orch: GridShiftOrchestrator, metadata: dict | None) -> dict:
    base = {
        "grid_threshold_mw": float(orch.grid.cfg.threshold_mw),
        "grid_baseline_source": "synthetic",
        "grid_baseline_min_mw": None,
        "grid_baseline_max_mw": None,
        "grid_eval_first_tick": None,
        "grid_eval_last_tick": None,
        "grid_eval_baseline_min_mw": None,
        "grid_eval_baseline_max_mw": None,
        "grid_threshold_window": None,
        "threshold_strategy": "synthetic_default",
        "threshold_headroom_mw": None,
    }
    if metadata:
        base.update(metadata)
    return base


def _submitted_work(orch: GridShiftOrchestrator) -> float:
    jobs = list(orch.fleet.completed) + list(orch.fleet.pending_jobs)
    for dc in orch.fleet.dcs.values():
        jobs.extend(dc.running_jobs)
        jobs.extend(dc.delayed_jobs)
    return sum(j.power_mw * j.base_duration_ticks for j in jobs)

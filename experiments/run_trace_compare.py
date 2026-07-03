"""Compare synthetic and derived trace-calibrated workload inputs."""

from __future__ import annotations

import os

from experiments.metrics import ensure_dir, summarize_runs, write_rows_csv
from experiments.trial_runner import TrialSpec, run_trial
from experiments.workloads import DerivedTraceWorkloadSource, SyntheticWorkloadSource

SEEDS = list(range(10))
TICKS = 50
INITIAL_BURST = 30
STEADY_BURST = 5
LOAD_SCALES = [1.0]
ROOT = os.path.dirname(os.path.dirname(__file__))
TRACE_PATH = os.path.join(
    ROOT,
    "data",
    "traces",
    "google_cluster_derived_5min.csv",
)
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")

POLICIES = ["none", "freeze", "directional"]

ATTACK_CFG = dict(
    compromised_fraction=0.34,
    attack_start_tick=15,
    lie_delta_mw=16.0,
    spike_mw=30.0,
    firmware_tamper=True,
    replay_nonce=False,
    key_compromise=False,
    detector_mode="fusion",
)

METRIC_KEYS = [
    "overload",
    "safe_mode_ticks",
    "bad_node_ticks",
    "migrations",
    "submitted_jobs",
    "completed_jobs",
    "total_submitted_work",
    "load_mismatch_pct",
    "completion_rate",
    "sla_violation_rate",
]


def _trace_workload(load_scale: float) -> DerivedTraceWorkloadSource:
    return DerivedTraceWorkloadSource(trace_path=TRACE_PATH, load_scale=load_scale)


def _sampled_work_units(source, *, seed: int, ticks: int) -> float:
    source.reset(seed)
    total = 0.0
    for tick in range(1, ticks + 1):
        jobs = source.jobs_for_tick(tick)
        if isinstance(jobs, int):
            total += jobs * source.avg_power_mw * source.avg_duration_ticks
        else:
            total += sum(job.power_mw * job.duration_ticks for job in jobs)
    return total


def _synthetic_workload(load_scale: float, *, seed: int) -> SyntheticWorkloadSource:
    trace_units = _trace_workload(load_scale).expected_work_units(TICKS)
    synthetic_base = SyntheticWorkloadSource(
        initial_burst=INITIAL_BURST,
        steady_burst=STEADY_BURST,
    )
    synthetic_units = synthetic_base.expected_work_units(TICKS)
    matched_scale = trace_units / synthetic_units if synthetic_units else load_scale
    trace_sampled_units = _sampled_work_units(
        _trace_workload(load_scale),
        seed=seed,
        ticks=TICKS,
    )
    return SyntheticWorkloadSource(
        initial_burst=INITIAL_BURST,
        steady_burst=STEADY_BURST,
        load_scale=matched_scale,
        target_work_units=trace_sampled_units,
        calibration_ticks=TICKS,
    )


def run_workload_trial(
    *,
    workload_source: str,
    policy: str,
    seed: int,
    load_scale: float = 1.0,
) -> dict:
    if workload_source == "synthetic":
        source = _synthetic_workload(load_scale, seed=seed)
    elif workload_source == "trace-calibrated":
        source = _trace_workload(load_scale)
    else:
        raise ValueError(f"Unknown workload_source: {workload_source}")

    row = run_trial(
        spec=TrialSpec(
            experiment="trace_compare",
            case=workload_source,
            policy=policy,
            detector_mode="fusion",
            seed=seed,
            attack_cfg=ATTACK_CFG,
            workload_source=source,
            ticks=TICKS,
        )
    )

    row["workload_source"] = workload_source
    row["load_scale"] = load_scale
    row["effective_load_scale"] = float(getattr(source, "load_scale", load_scale))
    return row


def _add_pair_load_mismatch(rows: list[dict]) -> None:
    grouped: dict[tuple, dict[str, dict]] = {}
    for row in rows:
        key = (row["policy"], row["seed"], row["load_scale"])
        grouped.setdefault(key, {})[row["workload_source"]] = row

    for pair in grouped.values():
        synthetic = pair.get("synthetic")
        trace = pair.get("trace-calibrated")
        if synthetic is None or trace is None:
            continue
        trace_work = trace["total_submitted_work"]
        mismatch = synthetic["total_submitted_work"] - trace_work
        denom = max(1.0, trace_work)
        mismatch_pct = 100.0 * mismatch / denom
        for row in (synthetic, trace):
            row["paired_trace_work"] = trace_work
            row["load_mismatch"] = mismatch
            row["load_mismatch_pct"] = mismatch_pct


def group_summary(rows: list[dict]) -> list[dict]:
    grouped: dict[tuple, list[dict]] = {}

    for row in rows:
        key = (
            row["workload_source"],
            row["policy"],
            row["detector_mode"],
            row["load_scale"],
        )
        grouped.setdefault(key, []).append(row)

    summary_rows: list[dict] = []

    for key in sorted(grouped):
        group_rows = grouped[key]
        metric_runs = [
            {metric: row[metric] for metric in METRIC_KEYS}
            for row in group_rows
        ]
        summary = summarize_runs(metric_runs)

        base = {
            "experiment": "trace_compare",
            "workload_source": key[0],
            "policy": key[1],
            "detector_mode": key[2],
            "load_scale": key[3],
            "n": len(group_rows),
        }

        for metric in METRIC_KEYS:
            base[f"{metric}_mean"] = summary.get(metric, {}).get("mean", 0.0)
            base[f"{metric}_ci"] = summary.get(metric, {}).get("ci", 0.0)

        summary_rows.append(base)

    return summary_rows


def main() -> None:
    ensure_dir(RESULTS_DIR)

    rows: list[dict] = []

    for load_scale in LOAD_SCALES:
        for policy in POLICIES:
            for seed in SEEDS:
                for workload_source in ["synthetic", "trace-calibrated"]:
                    rows.append(
                        run_workload_trial(
                            workload_source=workload_source,
                            policy=policy,
                            seed=seed,
                            load_scale=load_scale,
                        )
                    )

    _add_pair_load_mismatch(rows)

    raw_csv = os.path.join(RESULTS_DIR, "trace_compare.csv")
    summary_csv = os.path.join(RESULTS_DIR, "trace_compare_summary.csv")

    write_rows_csv(raw_csv, rows)
    write_rows_csv(summary_csv, group_summary(rows))

    mismatches = [abs(row["load_mismatch_pct"]) for row in rows]
    max_mismatch = max(mismatches) if mismatches else 0.0
    print("wrote:", raw_csv)
    print("wrote:", summary_csv)
    print("rows:", len(rows))
    print(f"max synthetic-vs-trace submitted-work mismatch: {max_mismatch:.2f}%")


if __name__ == "__main__":
    main()

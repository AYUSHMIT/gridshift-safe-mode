"""Compare synthetic and trace-calibrated workload inputs."""

from __future__ import annotations

import os

from experiments.metrics import ensure_dir, summarize_runs, write_rows_csv
from experiments.trial_runner import TrialSpec, run_trial
from experiments.workloads import SyntheticWorkloadSource, TraceWorkloadSource

SEEDS = list(range(10))
TICKS = 50
INITIAL_BURST = 30
STEADY_BURST = 5
ROOT = os.path.dirname(os.path.dirname(__file__))
TRACE_PATH = os.path.join(ROOT, "data", "traces", "google_power_sample.csv")
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
    "overload_exceedance",
    "safe_mode_ticks",
    "bad_node_ticks",
    "migrations",
    "completion_rate",
    "sla_violation_rate",
]


SYNTHETIC_WORKLOAD = SyntheticWorkloadSource(
    initial_burst=INITIAL_BURST,
    steady_burst=STEADY_BURST,
)
TRACE_WORKLOAD = TraceWorkloadSource(trace_path=TRACE_PATH)


def run_workload_trial(*, workload_source: str, policy: str, seed: int) -> dict:
    source = SYNTHETIC_WORKLOAD if workload_source == "synthetic" else TRACE_WORKLOAD
    if workload_source not in {"synthetic", "trace"}:
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
    return row


def group_summary(rows: list[dict]) -> list[dict]:
    grouped: dict[tuple, list[dict]] = {}

    for row in rows:
        key = (row["workload_source"], row["policy"], row["detector_mode"])
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

    for workload_source in ["synthetic", "trace"]:
        for policy in POLICIES:
            for seed in SEEDS:
                rows.append(
                    run_workload_trial(
                        workload_source=workload_source,
                        policy=policy,
                        seed=seed,
                    )
                )

    raw_csv = os.path.join(RESULTS_DIR, "trace_compare.csv")
    summary_csv = os.path.join(RESULTS_DIR, "trace_compare_summary.csv")

    write_rows_csv(raw_csv, rows)
    write_rows_csv(summary_csv, group_summary(rows))

    print("wrote:", raw_csv)
    print("wrote:", summary_csv)
    print("rows:", len(rows))


if __name__ == "__main__":
    main()
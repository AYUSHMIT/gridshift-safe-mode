"""Trace phase x compute headroom experiment runner."""

from __future__ import annotations

import argparse
import os

from experiments.metrics import ensure_dir, summarize_runs, write_rows_csv
from experiments.run_trace_compare import (
    POLICIES,
    RESULTS_DIR,
    _grid_options,
    run_workload_trial,
)


PHASES = {
    "post_burst": 1,
    "burst_peak": 4,
    "burst_onset": 15,
    "pre_burst": 18,
}

DEFAULT_SEEDS = list(range(10))
DEFAULT_CAPACITY_LEVELS = [128.0, 160.0, 192.0, 224.0]
WORKLOAD_SOURCE = "trace-calibrated"
DETECTOR_MODE = "fusion"

RAW_COLUMNS = [
    "experiment",
    "phase",
    "trace_start_tick",
    "region_capacity_mw",
    "policy",
    "detector_mode",
    "seed",
    "workload_source",
    "attack_start_tick",
    "active_arrival_first_tick",
    "active_arrival_last_tick",
    "submitted_jobs",
    "completed_jobs",
    "total_submitted_work",
    "completion_rate",
    "sla_violation_rate",
    "overload",
    "migrations",
    "migration_candidates_considered",
    "candidates_with_trusted_feasible_destination",
    "candidates_blocked_insufficient_destination_capacity",
    "migration_feasibility_rate",
    "grid_threshold_mw",
    "grid_eval_first_tick",
    "grid_eval_last_tick",
    "grid_eval_baseline_min_mw",
    "grid_eval_baseline_max_mw",
    "threshold_strategy",
    "threshold_headroom_mw",
    "grid_threshold_window",
]

GROUP_KEYS = [
    "phase",
    "trace_start_tick",
    "region_capacity_mw",
    "policy",
    "detector_mode",
    "workload_source",
]

SUMMARY_METRICS = [
    key for key in RAW_COLUMNS
    if key not in set(
        GROUP_KEYS
        + [
            "experiment",
            "seed",
            "workload_source",
            "threshold_strategy",
            "grid_threshold_window",
        ]
    )
]


def _parse_int_list(value: str) -> list[int]:
    return [int(part.strip()) for part in value.split(",") if part.strip()]


def _parse_float_list(value: str) -> list[float]:
    return [float(part.strip()) for part in value.split(",") if part.strip()]


def _parse_phases(value: str) -> list[str]:
    if value.strip().lower() == "all":
        return list(PHASES)
    phases = [part.strip() for part in value.split(",") if part.strip()]
    unknown = sorted(set(phases) - set(PHASES))
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown phase(s): {', '.join(unknown)}; expected all or "
            f"{', '.join(PHASES)}"
        )
    return phases


def _ordered_row(row: dict) -> dict:
    return {key: row.get(key) for key in RAW_COLUMNS}


def _summary_rows(rows: list[dict]) -> list[dict]:
    grouped: dict[tuple, list[dict]] = {}
    for row in rows:
        key = tuple(row[group_key] for group_key in GROUP_KEYS)
        grouped.setdefault(key, []).append(row)

    summary_rows = []
    for key in sorted(grouped):
        group_rows = grouped[key]
        metric_runs = [
            {metric: row[metric] for metric in SUMMARY_METRICS}
            for row in group_rows
        ]
        summary = summarize_runs(metric_runs)
        out = {"experiment": "trace_phase_headroom"}
        out.update(dict(zip(GROUP_KEYS, key)))
        out["n"] = len(group_rows)
        out["threshold_strategy"] = group_rows[0].get("threshold_strategy")
        out["grid_threshold_window"] = group_rows[0].get("grid_threshold_window")
        for metric in SUMMARY_METRICS:
            out[f"{metric}_mean"] = summary.get(metric, {}).get("mean", 0.0)
            out[f"{metric}_ci"] = summary.get(metric, {}).get("ci", 0.0)
        summary_rows.append(out)
    return summary_rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run trace phase x compute headroom GridShift trials."
    )
    parser.add_argument(
        "--seeds",
        type=_parse_int_list,
        default=DEFAULT_SEEDS,
        help="Comma-separated seed list. Defaults to 0,1,2,3,4,5,6,7,8,9.",
    )
    parser.add_argument(
        "--capacity-levels",
        type=_parse_float_list,
        default=DEFAULT_CAPACITY_LEVELS,
        help="Comma-separated region_capacity_mw values. Defaults to 128,160,192,224.",
    )
    parser.add_argument(
        "--phases",
        type=_parse_phases,
        default=list(PHASES),
        help="Comma-separated phase names or all. Defaults to all.",
    )
    parser.add_argument(
        "--experiment-ticks",
        type=int,
        default=50,
        help="Number of 5-minute simulation ticks to execute.",
    )
    parser.add_argument(
        "--attack-start-tick",
        type=int,
        default=15,
        help="Trust attack start tick.",
    )
    parser.add_argument(
        "--grid-trace-path",
        default=os.path.join(
            "data", "grid", "iso_ne_grid_derived_5min.csv"
        ),
        help="Derived ISO-NE grid trace path.",
    )
    parser.add_argument(
        "--grid-trace-start-tick",
        type=int,
        default=178,
        help="Grid trace tick consumed by simulation tick 1.",
    )
    parser.set_defaults(grid_threshold_mw=None)
    parser.add_argument(
        "--grid-threshold-headroom-mw",
        type=float,
        default=0.0,
        help="MW headroom added to the selected grid baseline maximum.",
    )
    parser.add_argument(
        "--grid-threshold-window",
        choices=["full", "eval"],
        default="eval",
        help="Baseline window for threshold headroom. Defaults to eval.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print matrix size and exit without running trials.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    grid_options = _grid_options(args)
    phases = args.phases
    seeds = args.seeds
    capacities = args.capacity_levels
    matrix_size = len(phases) * len(capacities) * len(POLICIES) * len(seeds)

    if args.dry_run:
        print("experiment: trace_phase_headroom")
        print("rows:", matrix_size)
        print("phases:", ",".join(phases))
        print("capacity_levels:", ",".join(str(c) for c in capacities))
        print("policies:", ",".join(POLICIES))
        print("seeds:", ",".join(str(seed) for seed in seeds))
        return

    ensure_dir(RESULTS_DIR)
    rows: list[dict] = []
    for phase in phases:
        trace_start_tick = PHASES[phase]
        for region_capacity_mw in capacities:
            for policy in POLICIES:
                for seed in seeds:
                    row = run_workload_trial(
                        experiment="trace_phase_headroom",
                        workload_source=WORKLOAD_SOURCE,
                        policy=policy,
                        seed=seed,
                        attack_start_tick=args.attack_start_tick,
                        trace_start_tick=trace_start_tick,
                        ticks=args.experiment_ticks,
                        grid_options=grid_options,
                        config_overrides={
                            "region_capacity_mw": region_capacity_mw,
                        },
                    )
                    row["phase"] = phase
                    row["region_capacity_mw"] = float(region_capacity_mw)
                    rows.append(_ordered_row(row))

    raw_csv = os.path.join(RESULTS_DIR, "trace_phase_headroom.csv")
    summary_csv = os.path.join(
        RESULTS_DIR, "trace_phase_headroom_summary.csv"
    )

    write_rows_csv(raw_csv, rows)
    write_rows_csv(summary_csv, _summary_rows(rows))

    print("wrote:", raw_csv)
    print("wrote:", summary_csv)
    print("rows:", len(rows))
    print("attack_start_tick:", args.attack_start_tick)
    print("experiment_ticks:", args.experiment_ticks)
    print("grid_threshold_mw:", grid_options["grid_metadata"]["grid_threshold_mw"])
    print("grid_trace_start_tick:", args.grid_trace_start_tick)
    print(
        "grid_eval_tick_range:",
        grid_options["grid_metadata"]["grid_eval_first_tick"],
        grid_options["grid_metadata"]["grid_eval_last_tick"],
    )
    print(
        "grid_threshold_window:",
        grid_options["grid_metadata"]["grid_threshold_window"],
    )


if __name__ == "__main__":
    main()

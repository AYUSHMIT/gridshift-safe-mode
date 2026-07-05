"""Run policy comparison with an HPC SWF-derived workload."""

from __future__ import annotations

import argparse
import os

from experiments.metrics import ensure_dir, summarize_runs, write_rows_csv
from experiments.run_trace_compare import (
    METRIC_KEYS,
    POLICIES,
    RESULTS_DIR,
    SEEDS,
    _grid_options,
    run_workload_trial,
)


WORKLOAD_SOURCE = "hpc-swf"


def _parse_int_list(value: str) -> list[int]:
    return [int(part.strip()) for part in value.split(",") if part.strip()]


def group_summary(rows: list[dict]) -> list[dict]:
    grouped: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (
            row["workload_source"],
            row["policy"],
            row["detector_mode"],
            row["swf_derived_path"],
            row["swf_trace_start_tick"],
            row["swf_simulation_start_tick"],
        )
        grouped.setdefault(key, []).append(row)

    summary_rows: list[dict] = []
    for key in sorted(grouped):
        group_rows = grouped[key]
        metric_runs = [
            {metric: row[metric] for metric in METRIC_KEYS if metric in row}
            for row in group_rows
        ]
        summary = summarize_runs(metric_runs)

        out = {
            "experiment": "swf_compare",
            "workload_source": key[0],
            "policy": key[1],
            "detector_mode": key[2],
            "swf_derived_path": key[3],
            "swf_trace_start_tick": key[4],
            "swf_simulation_start_tick": key[5],
            "grid_baseline_source": group_rows[0].get("grid_baseline_source"),
            "threshold_strategy": group_rows[0].get("threshold_strategy"),
            "grid_threshold_window": group_rows[0].get("grid_threshold_window"),
            "n": len(group_rows),
        }
        for metric in METRIC_KEYS:
            out[f"{metric}_mean"] = summary.get(metric, {}).get("mean", 0.0)
            out[f"{metric}_ci"] = summary.get(metric, {}).get("ci", 0.0)
        summary_rows.append(out)
    return summary_rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare GridShift policies using an HPC SWF-derived workload."
    )
    parser.add_argument(
        "--swf-derived-path",
        required=True,
        help="Derived SWF workload CSV produced by experiments.build_swf_summary.",
    )
    parser.add_argument(
        "--seeds",
        type=_parse_int_list,
        default=SEEDS,
        help="Comma-separated seed list. Defaults to 0,1,2,3,4,5,6,7,8,9.",
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
        "--swf-trace-start-tick",
        type=int,
        default=None,
        help=(
            "Native/source tick in the SWF-derived CSV where the selected "
            "workload window begins. Defaults to the derived CSV's first tick."
        ),
    )
    parser.add_argument(
        "--swf-simulation-start-tick",
        type=int,
        default=1,
        help=(
            "Simulation tick where --swf-trace-start-tick should be aligned. "
            "Defaults to 1."
        ),
    )
    parser.add_argument(
        "--grid-trace-path",
        default=os.path.join("data", "grid", "iso_ne_grid_derived_5min.csv"),
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
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    ensure_dir(RESULTS_DIR)
    grid_options = _grid_options(args)

    rows: list[dict] = []
    for policy in POLICIES:
        for seed in args.seeds:
            row = run_workload_trial(
                experiment="swf_compare",
                workload_source=WORKLOAD_SOURCE,
                policy=policy,
                seed=seed,
                attack_start_tick=args.attack_start_tick,
                ticks=args.experiment_ticks,
                grid_options=grid_options,
                swf_derived_path=args.swf_derived_path,
                swf_trace_start_tick=args.swf_trace_start_tick,
                swf_simulation_start_tick=args.swf_simulation_start_tick,
            )
            _validate_nonempty_workload(row)
            row["swf_derived_path"] = args.swf_derived_path
            rows.append(row)

    raw_csv = os.path.join(RESULTS_DIR, "swf_compare.csv")
    summary_csv = os.path.join(RESULTS_DIR, "swf_compare_summary.csv")
    write_rows_csv(raw_csv, rows)
    write_rows_csv(summary_csv, group_summary(rows))

    print("wrote:", raw_csv)
    print("wrote:", summary_csv)
    print("rows:", len(rows))
    print("attack_start_tick:", args.attack_start_tick)
    print("experiment_ticks:", args.experiment_ticks)
    print("swf_derived_path:", args.swf_derived_path)
    print("swf_trace_start_tick:", rows[0]["swf_trace_start_tick"] if rows else None)
    print(
        "swf_simulation_start_tick:",
        rows[0]["swf_simulation_start_tick"] if rows else None,
    )
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


def _validate_nonempty_workload(row: dict) -> None:
    if row["submitted_jobs"] > 0 and row["total_submitted_work"] > 0:
        return
    raise RuntimeError(
        "Selected SWF window did not overlap active simulation ticks: "
        f"swf_trace_start_tick={row.get('swf_trace_start_tick')}, "
        f"swf_simulation_start_tick={row.get('swf_simulation_start_tick')}, "
        f"experiment_ticks={row.get('experiment_ticks')}, "
        f"submitted_jobs={row.get('submitted_jobs')}, "
        f"total_submitted_work={row.get('total_submitted_work')}. "
        "Choose a native SWF tick with arrivals or adjust the simulation "
        "alignment tick."
    )


if __name__ == "__main__":
    main()

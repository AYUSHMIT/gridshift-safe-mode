"""Run SWF policy validation over multiple aligned source windows."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from experiments.build_swf_summary import build_swf_summary
from experiments.metrics import ensure_dir, write_rows_csv
from experiments.run_trace_compare import (
    RESULTS_DIR,
    _grid_options,
    run_workload_trial,
)
from experiments.trace_loader import load_derived_trace


DEFAULT_DERIVED_OUTPUT = "/tmp/gridshift_swf_multiwindow_derived.csv"
DEFAULT_GRID_TRACE_PATH = os.path.join("data", "grid", "iso_ne_grid_derived_5min.csv")
DEFAULT_POLICIES = ["none", "freeze", "directional"]
WORKLOAD_SOURCE = "hpc-swf"


def _parse_int_list(value: str) -> list[int]:
    return [int(part.strip()) for part in value.split(",") if part.strip()]


def _parse_str_list(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _select_windows(
    derived_path: str,
    *,
    num_windows: int,
    window_length: int,
) -> list[int]:
    if num_windows < 1:
        raise ValueError("--num-windows must be >= 1")
    if window_length < 1:
        raise ValueError("--window-length must be >= 1")

    points = [point for point in load_derived_trace(derived_path) if point.arrivals > 0]
    if not points:
        raise ValueError(f"No active arrivals found in derived SWF trace: {derived_path}")

    active_ticks = sorted(point.tick for point in points)
    first_tick = active_ticks[0]
    last_start = max(first_tick, active_ticks[-1] - window_length + 1)
    anchors = (
        [first_tick]
        if num_windows == 1
        else [
            round(first_tick + index * (last_start - first_tick) / (num_windows - 1))
            for index in range(num_windows)
        ]
    )

    starts: list[int] = []
    for anchor in anchors:
        min_allowed = starts[-1] + window_length if starts else first_tick
        candidate = max(int(anchor), min_allowed)
        selected = None
        for tick in active_ticks:
            if tick < candidate:
                continue
            if starts and tick < starts[-1] + window_length:
                continue
            selected = tick
            break
        if selected is None:
            raise ValueError(
                "Could not select enough non-overlapping active SWF windows; "
                f"selected={len(starts)}, requested={num_windows}"
            )
        starts.append(int(selected))
    return starts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run multi-window SWF validation with aligned source windows."
    )
    parser.add_argument(
        "--swf-path",
        required=True,
        help="Local canonical .swf file. Keep real traces outside git.",
    )
    parser.add_argument(
        "--num-windows",
        type=int,
        default=5,
        help="Number of non-overlapping active source windows to select.",
    )
    parser.add_argument(
        "--window-length",
        type=int,
        default=50,
        help="Simulation/source window length in 5-minute ticks.",
    )
    parser.add_argument(
        "--seeds",
        type=_parse_int_list,
        default=[0, 1, 2],
        help="Comma-separated seed list. Defaults to 0,1,2.",
    )
    parser.add_argument(
        "--policies",
        type=_parse_str_list,
        default=DEFAULT_POLICIES,
        help="Comma-separated policy list. Defaults to none,freeze,directional.",
    )
    parser.add_argument(
        "--output",
        default=os.path.join(RESULTS_DIR, "swf_multiwindow_raw.csv"),
        help="Output raw CSV path.",
    )
    parser.add_argument(
        "--derived-output",
        default=DEFAULT_DERIVED_OUTPUT,
        help=f"Temporary derived SWF CSV path. Defaults to {DEFAULT_DERIVED_OUTPUT}.",
    )
    parser.add_argument(
        "--grid-trace-path",
        default=DEFAULT_GRID_TRACE_PATH,
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
        "--attack-start-tick",
        type=int,
        default=15,
        help="Trust attack start tick.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    swf_path = Path(args.swf_path)
    if not swf_path.exists():
        raise FileNotFoundError(f"SWF file not found: {swf_path}")

    build_swf_summary(input_path=str(swf_path), output_path=args.derived_output)
    starts = _select_windows(
        args.derived_output,
        num_windows=args.num_windows,
        window_length=args.window_length,
    )

    args.experiment_ticks = args.window_length
    grid_options = _grid_options(args)

    rows: list[dict] = []
    for window_id, swf_start_tick in enumerate(starts):
        for policy in args.policies:
            for seed in args.seeds:
                row = run_workload_trial(
                    experiment="swf_multiwindow",
                    workload_source=WORKLOAD_SOURCE,
                    policy=policy,
                    seed=seed,
                    attack_start_tick=args.attack_start_tick,
                    ticks=args.window_length,
                    grid_options=grid_options,
                    swf_derived_path=args.derived_output,
                    swf_trace_start_tick=swf_start_tick,
                    swf_simulation_start_tick=1,
                )
                if row["submitted_jobs"] <= 0 or row["total_submitted_work"] <= 0:
                    raise RuntimeError(
                        "Selected SWF window produced no workload: "
                        f"window_id={window_id}, swf_start_tick={swf_start_tick}"
                    )
                row["window_id"] = window_id
                row["swf_start_tick"] = swf_start_tick
                row["swf_window_length"] = args.window_length
                row["swf_derived_path"] = args.derived_output
                rows.append(row)

    ensure_dir(os.path.dirname(args.output))
    write_rows_csv(args.output, rows)
    print("wrote:", args.output)
    print("rows:", len(rows))
    print("window_starts:", ",".join(str(start) for start in starts))
    print("window_length:", args.window_length)
    print("policies:", ",".join(args.policies))
    print("seeds:", ",".join(str(seed) for seed in args.seeds))


if __name__ == "__main__":
    main()

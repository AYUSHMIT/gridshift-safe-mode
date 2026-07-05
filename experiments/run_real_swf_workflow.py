"""Build a local real SWF workload summary, then run the SWF comparison."""

from __future__ import annotations

import argparse
from pathlib import Path

from experiments.build_swf_summary import build_swf_summary
from experiments.run_swf_compare import main as run_swf_compare_main
from experiments.trace_loader import load_derived_trace


DEFAULT_DERIVED_OUTPUT = "/tmp/gridshift_real_swf_derived.csv"
DEFAULT_GRID_TRACE_PATH = "data/grid/iso_ne_grid_derived_5min.csv"
DEFAULT_SEEDS = "0,1,2,3,4,5,6,7,8,9"


def _print_dry_run(args: argparse.Namespace) -> None:
    swf_trace_start = (
        str(args.swf_trace_start_tick)
        if args.swf_trace_start_tick is not None
        else "<first-active-derived-tick>"
    )
    print("would run:")
    print(
        "python -m experiments.build_swf_summary "
        f"--input {args.swf_path} "
        f"--output {args.derived_output}"
    )
    print(
        "python -m experiments.run_swf_compare "
        f"--swf-derived-path {args.derived_output} "
        f"--seeds {DEFAULT_SEEDS} "
        f"--swf-trace-start-tick {swf_trace_start} "
        f"--swf-simulation-start-tick {args.swf_simulation_start_tick} "
        f"--grid-trace-path {DEFAULT_GRID_TRACE_PATH} "
        "--grid-trace-start-tick 178 "
        "--grid-threshold-headroom-mw 0 "
        "--grid-threshold-window eval"
    )
    print("outputs: experiments/results/swf_compare*.csv")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare a local real SWF trace and run GridShift's standard "
            "SWF policy comparison."
        )
    )
    parser.add_argument(
        "--swf-path",
        required=True,
        help="Local canonical .swf file. Keep real traces outside git.",
    )
    parser.add_argument(
        "--derived-output",
        default=DEFAULT_DERIVED_OUTPUT,
        help=(
            "Temporary derived workload CSV. Defaults to "
            f"{DEFAULT_DERIVED_OUTPUT}."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the workflow commands without building or running trials.",
    )
    parser.add_argument(
        "--swf-trace-start-tick",
        type=int,
        default=None,
        help=(
            "Native/source tick in the derived SWF CSV where the workload "
            "window begins. Defaults to the first active derived tick."
        ),
    )
    parser.add_argument(
        "--swf-simulation-start-tick",
        type=int,
        default=1,
        help="Simulation tick where the selected SWF window begins.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.dry_run:
        _print_dry_run(args)
        return

    swf_path = Path(args.swf_path)
    if not swf_path.exists():
        raise FileNotFoundError(f"SWF file not found: {swf_path}")

    build_swf_summary(
        input_path=str(swf_path),
        output_path=args.derived_output,
    )
    swf_trace_start_tick = (
        args.swf_trace_start_tick
        if args.swf_trace_start_tick is not None
        else _first_active_tick(args.derived_output)
    )
    print("swf_trace_start_tick:", swf_trace_start_tick)
    print("swf_simulation_start_tick:", args.swf_simulation_start_tick)
    run_swf_compare_main(
        [
            "--swf-derived-path",
            args.derived_output,
            "--seeds",
            DEFAULT_SEEDS,
            "--swf-trace-start-tick",
            str(swf_trace_start_tick),
            "--swf-simulation-start-tick",
            str(args.swf_simulation_start_tick),
            "--grid-trace-path",
            DEFAULT_GRID_TRACE_PATH,
            "--grid-trace-start-tick",
            "178",
            "--grid-threshold-headroom-mw",
            "0",
            "--grid-threshold-window",
            "eval",
        ]
    )


def _first_active_tick(derived_path: str) -> int:
    for point in load_derived_trace(derived_path):
        if point.arrivals > 0:
            return int(point.tick)
    raise ValueError(f"No active arrivals found in derived SWF trace: {derived_path}")


if __name__ == "__main__":
    main()

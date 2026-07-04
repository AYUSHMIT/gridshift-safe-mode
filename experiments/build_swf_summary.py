"""Build GridShift's derived trace format from an HPC SWF workload log.

The Standard Workload Format (SWF) stores one whitespace-delimited job record
with 18 fields. This builder uses:

    2. submit time
    4. run time
    5. allocated processors
    8. requested processors

Submit time is converted to 5-minute GridShift ticks, runtime is converted to
duration ticks, and processor demand is normalized into cpu_demand_norm.
Unlike the Google event-sample path, SWF contains runtime information, so the
derived duration_p50 and duration_p90 values come from the trace itself.
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from dataclasses import dataclass
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from experiments.trace_loader import load_derived_trace

DERIVED_FIELDNAMES = [
    "tick",
    "arrivals",
    "cpu_demand_norm",
    "duration_p50",
    "duration_p90",
    "priority_high_frac",
    "latency_sensitive_frac",
]


@dataclass(frozen=True)
class SwfJob:
    submit_seconds: float
    duration_ticks: float
    processor_demand: float


@dataclass
class TickBucket:
    arrivals: int
    cpu_values: list[float]
    durations: list[float]


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("Cannot compute percentile of an empty list")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[int(rank)]
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _parse_swf_jobs(
    input_path: Path,
    *,
    tick_seconds: float,
    min_duration_ticks: float,
    skip_non_positive_runtime: bool,
    skip_non_positive_cpu: bool,
) -> list[SwfJob]:
    jobs: list[SwfJob] = []
    with input_path.open(encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith(";"):
                continue
            fields = stripped.split()
            if len(fields) != 18:
                raise ValueError(
                    f"{input_path}:{lineno}: expected 18 SWF fields, "
                    f"found {len(fields)}"
                )

            try:
                submit_seconds = float(fields[1])
                run_seconds = float(fields[3])
                allocated_processors = float(fields[4])
                requested_processors = float(fields[7])
            except ValueError as exc:
                raise ValueError(
                    f"{input_path}:{lineno}: could not parse numeric SWF fields"
                ) from exc

            if submit_seconds < 0:
                raise ValueError(
                    f"{input_path}:{lineno}: negative submit time {submit_seconds}"
                )

            if run_seconds <= 0:
                if skip_non_positive_runtime:
                    continue
                run_seconds = tick_seconds * min_duration_ticks

            processor_demand = (
                requested_processors
                if requested_processors > 0
                else allocated_processors
            )
            if processor_demand <= 0:
                if skip_non_positive_cpu:
                    continue
                raise ValueError(
                    f"{input_path}:{lineno}: non-positive processor demand "
                    f"{processor_demand}"
                )

            jobs.append(
                SwfJob(
                    submit_seconds=submit_seconds,
                    duration_ticks=max(
                        min_duration_ticks,
                        run_seconds / tick_seconds,
                    ),
                    processor_demand=processor_demand,
                )
            )

    if not jobs:
        raise ValueError(f"No usable SWF jobs found in {input_path}")
    return jobs


def build_swf_summary(
    *,
    input_path: str,
    output_path: str,
    tick_seconds: float = 300.0,
    epoch_start: float | None = None,
    start_tick: int = 1,
    cpu_normalization_max: float | None = None,
    min_duration_ticks: float = 1.0,
    priority_high_frac: float = 0.0,
    latency_sensitive_frac: float = 0.0,
    skip_non_positive_runtime: bool = False,
    skip_non_positive_cpu: bool = False,
) -> None:
    """Aggregate an SWF workload log into GridShift derived trace format."""
    source = Path(input_path)
    if not source.exists():
        raise FileNotFoundError(f"Input SWF not found: {source}")
    if tick_seconds <= 0:
        raise ValueError("--tick-seconds must be positive")
    if min_duration_ticks <= 0:
        raise ValueError("--min-duration-ticks must be positive")
    if start_tick < 0:
        raise ValueError("--start-tick must be non-negative")
    for name, value in (
        ("--priority-high-frac", priority_high_frac),
        ("--latency-sensitive-frac", latency_sensitive_frac),
    ):
        if value < 0.0 or value > 1.0:
            raise ValueError(f"{name} must be within [0, 1]")

    jobs = _parse_swf_jobs(
        source,
        tick_seconds=tick_seconds,
        min_duration_ticks=min_duration_ticks,
        skip_non_positive_runtime=skip_non_positive_runtime,
        skip_non_positive_cpu=skip_non_positive_cpu,
    )

    origin = (
        min(job.submit_seconds for job in jobs)
        if epoch_start is None
        else epoch_start
    )
    cpu_max = cpu_normalization_max
    if cpu_max is None:
        cpu_max = max(job.processor_demand for job in jobs)
    if cpu_max <= 0:
        raise ValueError("--cpu-normalization-max must be positive")

    buckets: dict[int, TickBucket] = {}
    for job in jobs:
        tick_offset = int(math.floor((job.submit_seconds - origin) / tick_seconds))
        if tick_offset < 0:
            continue
        tick = start_tick + tick_offset
        bucket = buckets.setdefault(
            tick,
            TickBucket(arrivals=0, cpu_values=[], durations=[]),
        )
        bucket.arrivals += 1
        bucket.cpu_values.append(
            min(1.0, max(0.0, job.processor_demand / cpu_max))
        )
        bucket.durations.append(job.duration_ticks)

    if not buckets:
        raise ValueError("No SWF jobs fell within non-negative simulation ticks")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=DERIVED_FIELDNAMES,
            lineterminator="\n",
        )
        writer.writeheader()
        for tick in sorted(buckets):
            bucket = buckets[tick]
            arrivals = bucket.arrivals
            writer.writerow(
                {
                    "tick": tick,
                    "arrivals": arrivals,
                    "cpu_demand_norm": round(
                        sum(bucket.cpu_values) / arrivals,
                        6,
                    ),
                    "duration_p50": round(_percentile(bucket.durations, 0.50), 6),
                    "duration_p90": round(_percentile(bucket.durations, 0.90), 6),
                    "priority_high_frac": round(priority_high_frac, 6),
                    "latency_sensitive_frac": round(latency_sensitive_frac, 6),
                }
            )

    points = load_derived_trace(output)
    print("wrote:", output)
    print("rows:", len(points))
    print("tick_range:", f"{points[0].tick}..{points[-1].tick}")
    print("total_arrivals:", int(sum(point.arrivals for point in points)))
    print("cpu_normalization_max:", cpu_max)
    print("validated_schema:", ",".join(DERIVED_FIELDNAMES))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate an HPC Standard Workload Format log into GridShift's "
            "derived 5-minute workload summary."
        )
    )
    parser.add_argument("--input", required=True, help="Input SWF workload log.")
    parser.add_argument(
        "--output",
        default="data/traces/swf_derived_5min.csv",
        help="Derived GridShift trace CSV to write.",
    )
    parser.add_argument("--tick-seconds", type=float, default=300.0)
    parser.add_argument(
        "--epoch-start",
        type=float,
        default=None,
        help="Optional absolute submit timestamp for the trace origin, seconds.",
    )
    parser.add_argument(
        "--start-tick",
        type=int,
        default=1,
        help="First emitted simulator tick.",
    )
    parser.add_argument("--cpu-normalization-max", type=float, default=None)
    parser.add_argument("--min-duration-ticks", type=float, default=1.0)
    parser.add_argument(
        "--priority-high-frac",
        type=float,
        default=0.0,
        help="SWF has no priority field; use this constant fraction.",
    )
    parser.add_argument(
        "--latency-sensitive-frac",
        type=float,
        default=0.0,
        help="SWF has no latency label; use this constant fraction.",
    )
    parser.add_argument("--skip-non-positive-runtime", action="store_true")
    parser.add_argument("--skip-non-positive-cpu", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    build_swf_summary(
        input_path=args.input,
        output_path=args.output,
        tick_seconds=args.tick_seconds,
        epoch_start=args.epoch_start,
        start_tick=args.start_tick,
        cpu_normalization_max=args.cpu_normalization_max,
        min_duration_ticks=args.min_duration_ticks,
        priority_high_frac=args.priority_high_frac,
        latency_sensitive_frac=args.latency_sensitive_frac,
        skip_non_positive_runtime=args.skip_non_positive_runtime,
        skip_non_positive_cpu=args.skip_non_positive_cpu,
    )


if __name__ == "__main__":
    main()

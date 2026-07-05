"""Build GridShift's derived trace format from an HPC SWF workload log.

Standard Workload Format records are whitespace-delimited. This builder uses
the canonical fields for submit time, runtime, allocated processors, and
requested processors, then emits the same derived CSV schema consumed by
DerivedTraceWorkloadSource.
"""
from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from pathlib import Path

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
    runtime_seconds: float
    processor_demand: float


@dataclass
class TickBucket:
    arrivals: int
    cpu_values: list[float]
    duration_ticks: list[float]


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("Cannot compute percentile of an empty list")
    if len(ordered) == 1:
        return ordered[0]

    rank = (len(ordered) - 1) * percentile
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]

    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _load_swf_jobs(path: Path) -> list[SwfJob]:
    jobs: list[SwfJob] = []
    with path.open(encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith(";"):
                continue

            fields = stripped.split()
            if len(fields) < 8:
                raise ValueError(
                    f"{path}:{lineno}: expected at least 8 SWF fields, "
                    f"found {len(fields)}"
                )

            try:
                submit_seconds = float(fields[1])
                runtime_seconds = float(fields[3])
                allocated_processors = float(fields[4])
                requested_processors = float(fields[7])
            except ValueError as exc:
                raise ValueError(
                    f"{path}:{lineno}: could not parse numeric SWF fields"
                ) from exc

            if submit_seconds < 0:
                raise ValueError(
                    f"{path}:{lineno}: submit_time must be non-negative"
                )
            if runtime_seconds <= 0:
                raise ValueError(f"{path}:{lineno}: run_time must be positive")

            processor_demand = (
                requested_processors
                if requested_processors > 0
                else allocated_processors
            )
            if processor_demand <= 0:
                raise ValueError(
                    f"{path}:{lineno}: processor demand must be positive"
                )

            jobs.append(
                SwfJob(
                    submit_seconds=submit_seconds,
                    runtime_seconds=runtime_seconds,
                    processor_demand=processor_demand,
                )
            )

    if not jobs:
        raise ValueError(f"No usable SWF job records found in {path}")
    return jobs


def build_swf_summary(
    *,
    input_path: str,
    output_path: str,
    tick_seconds: float = 300.0,
    cpu_normalization_max: float | None = None,
    priority_high_frac: float = 0.0,
    latency_sensitive_frac: float = 0.0,
) -> None:
    if tick_seconds <= 0:
        raise ValueError("--tick-seconds must be positive")
    for name, value in (
        ("--priority-high-frac", priority_high_frac),
        ("--latency-sensitive-frac", latency_sensitive_frac),
    ):
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be within [0, 1]")

    source = Path(input_path)
    if not source.exists():
        raise FileNotFoundError(f"Input SWF not found: {source}")

    jobs = _load_swf_jobs(source)
    cpu_max = (
        cpu_normalization_max
        if cpu_normalization_max is not None
        else max(job.processor_demand for job in jobs)
    )
    if cpu_max <= 0:
        raise ValueError("--cpu-normalization-max must be positive")

    buckets: dict[int, TickBucket] = {}
    for job in jobs:
        tick = int(math.floor(job.submit_seconds / tick_seconds)) + 1
        bucket = buckets.setdefault(
            tick,
            TickBucket(arrivals=0, cpu_values=[], duration_ticks=[]),
        )
        bucket.arrivals += 1
        bucket.cpu_values.append(
            min(1.0, max(0.0, job.processor_demand / cpu_max))
        )
        bucket.duration_ticks.append(max(1.0, job.runtime_seconds / tick_seconds))

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
            writer.writerow(
                {
                    "tick": tick,
                    "arrivals": bucket.arrivals,
                    "cpu_demand_norm": round(
                        sum(bucket.cpu_values) / bucket.arrivals,
                        6,
                    ),
                    "duration_p50": round(
                        _percentile(bucket.duration_ticks, 0.50),
                        6,
                    ),
                    "duration_p90": round(
                        _percentile(bucket.duration_ticks, 0.90),
                        6,
                    ),
                    "priority_high_frac": round(priority_high_frac, 6),
                    "latency_sensitive_frac": round(
                        latency_sensitive_frac,
                        6,
                    ),
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
        required=True,
        help="Derived GridShift workload CSV to write.",
    )
    parser.add_argument(
        "--tick-seconds",
        type=float,
        default=300.0,
        help="Seconds represented by one GridShift simulation tick.",
    )
    parser.add_argument(
        "--cpu-normalization-max",
        type=float,
        default=None,
        help="Optional processor demand denominator.",
    )
    parser.add_argument(
        "--priority-high-frac",
        type=float,
        default=0.0,
        help="SWF has no priority label; use this constant fraction.",
    )
    parser.add_argument(
        "--latency-sensitive-frac",
        type=float,
        default=0.0,
        help="SWF has no latency label; use this constant fraction.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    build_swf_summary(
        input_path=args.input,
        output_path=args.output,
        tick_seconds=args.tick_seconds,
        cpu_normalization_max=args.cpu_normalization_max,
        priority_high_frac=args.priority_high_frac,
        latency_sensitive_frac=args.latency_sensitive_frac,
    )


if __name__ == "__main__":
    main()

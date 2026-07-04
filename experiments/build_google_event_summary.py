"""Build a derived GridShift trace from a ClusterData event-sample CSV.

This script consumes the event-sample export shape:

    trace_time_us,collection_id,instance_index,cpu_request,priority,
    latency_sensitive,minute_bucket

It aggregates event arrivals into 5-minute simulator ticks and writes the same
derived schema consumed by the trace-calibrated simulator:

    tick,arrivals,cpu_demand_norm,duration_p50,duration_p90,
    priority_high_frac,latency_sensitive_frac

This is event-arrival calibration, not full runtime replay. The event sample
does not contain actual runtimes, so duration_p50 and duration_p90 are explicit
parameters. CPU demand is normalized globally and written as the mean normalized
CPU demand per tick.
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

TRUTHY = {"1", "true", "t", "yes", "y"}


@dataclass
class EventBucket:
    arrivals: int
    cpu_values: list[float]
    high_priority: int
    latency_sensitive: int


def _parse_float(row: dict[str, str], column: str, source: Path) -> float:
    try:
        return float(row[column])
    except KeyError as exc:
        raise ValueError(f"Missing column {column!r} in {source}") from exc
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Could not parse {column!r} value {row.get(column)!r} in {source}"
        ) from exc


def _row_bool(row: dict[str, str], column: str) -> bool:
    return str(row.get(column, "")).strip().lower() in TRUTHY


def build_google_event_summary(
    *,
    input_path: str,
    output_path: str,
    time_column: str = "trace_time_us",
    cpu_column: str = "cpu_request",
    priority_column: str = "priority",
    latency_column: str = "latency_sensitive",
    tick_seconds: float = 300.0,
    epoch_start_us: float | None = None,
    cpu_already_normalized: bool = False,
    cpu_normalization_max: float | None = None,
    priority_high_threshold: float = 120.0,
    default_duration_p50: float = 1.0,
    default_duration_p90: float = 3.0,
    skip_non_positive_cpu: bool = False,
) -> None:
    """Aggregate an event-sample CSV into GridShift derived trace format."""
    source = Path(input_path)
    if not source.exists():
        raise FileNotFoundError(f"Input CSV not found: {source}")
    if tick_seconds <= 0:
        raise ValueError("--tick-seconds must be positive")
    if default_duration_p50 <= 0 or default_duration_p90 <= 0:
        raise ValueError("Default durations must be positive")
    if default_duration_p90 < default_duration_p50:
        raise ValueError("--default-duration-p90 must be >= --default-duration-p50")

    events: list[dict[str, float | bool]] = []
    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"Input CSV is empty: {source}")
        required = {time_column, cpu_column, priority_column, latency_column}
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(f"Input CSV is missing columns: {sorted(missing)}")

        for row in reader:
            trace_time_us = _parse_float(row, time_column, source)
            cpu = _parse_float(row, cpu_column, source)
            priority = _parse_float(row, priority_column, source)
            if trace_time_us < 0:
                raise ValueError(f"Negative trace_time_us in {source}: {trace_time_us}")
            if cpu < 0:
                raise ValueError(f"Negative cpu_request in {source}: {cpu}")
            if cpu == 0 and skip_non_positive_cpu:
                continue
            events.append(
                {
                    "trace_time_us": trace_time_us,
                    "cpu": cpu,
                    "priority": priority,
                    "latency_sensitive": _row_bool(row, latency_column),
                }
            )

    if not events:
        raise ValueError(f"No usable events found in {source}")

    origin_us = epoch_start_us
    if origin_us is None:
        origin_us = min(float(event["trace_time_us"]) for event in events)

    cpu_max = cpu_normalization_max
    if cpu_already_normalized:
        cpu_max = 1.0
    elif cpu_max is None:
        cpu_max = max(float(event["cpu"]) for event in events)
    if cpu_max <= 0:
        raise ValueError("CPU normalization max must be positive")

    tick_us = tick_seconds * 1_000_000.0
    buckets: dict[int, EventBucket] = {}
    for event in events:
        tick = int(math.floor((float(event["trace_time_us"]) - origin_us) / tick_us))
        if tick < 0:
            continue
        bucket = buckets.setdefault(
            tick,
            EventBucket(
                arrivals=0,
                cpu_values=[],
                high_priority=0,
                latency_sensitive=0,
            ),
        )
        bucket.arrivals += 1
        bucket.cpu_values.append(min(1.0, max(0.0, float(event["cpu"]) / cpu_max)))
        bucket.high_priority += int(float(event["priority"]) >= priority_high_threshold)
        bucket.latency_sensitive += int(bool(event["latency_sensitive"]))

    if not buckets:
        raise ValueError("No events fell within non-negative simulation ticks")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DERIVED_FIELDNAMES)
        writer.writeheader()
        for tick in sorted(buckets):
            bucket = buckets[tick]
            arrivals = bucket.arrivals
            writer.writerow(
                {
                    "tick": tick,
                    "arrivals": arrivals,
                    "cpu_demand_norm": round(sum(bucket.cpu_values) / arrivals, 6),
                    "duration_p50": default_duration_p50,
                    "duration_p90": default_duration_p90,
                    "priority_high_frac": round(bucket.high_priority / arrivals, 6),
                    "latency_sensitive_frac": round(
                        bucket.latency_sensitive / arrivals,
                        6,
                    ),
                }
            )

    points = load_derived_trace(output)
    print("wrote:", output)
    print("rows:", len(points))
    print("tick_range:", f"{points[0].tick}..{points[-1].tick}")
    print("total_arrivals:", int(sum(point.arrivals for point in points)))
    print("cpu_demand_norm:", "mean normalized CPU demand per tick")
    print(
        "duration_defaults:",
        f"p50={default_duration_p50}, p90={default_duration_p90}",
    )
    print("validated_schema:", ",".join(DERIVED_FIELDNAMES))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate a Google ClusterData2019 event-sample CSV into "
            "GridShift's derived 5-minute workload summary."
        )
    )
    parser.add_argument("--input", required=True, help="Event-sample CSV.")
    parser.add_argument(
        "--output",
        default="data/traces/google_cluster_derived_5min.csv",
        help="Derived GridShift trace CSV to write.",
    )
    parser.add_argument("--time-column", default="trace_time_us")
    parser.add_argument("--cpu-column", default="cpu_request")
    parser.add_argument("--priority-column", default="priority")
    parser.add_argument("--latency-column", default="latency_sensitive")
    parser.add_argument("--tick-seconds", type=float, default=300.0)
    parser.add_argument("--epoch-start-us", type=float, default=None)
    parser.add_argument("--cpu-already-normalized", action="store_true")
    parser.add_argument("--cpu-normalization-max", type=float, default=None)
    parser.add_argument("--priority-high-threshold", type=float, default=120.0)
    parser.add_argument("--default-duration-p50", type=float, default=1.0)
    parser.add_argument("--default-duration-p90", type=float, default=3.0)
    parser.add_argument("--skip-non-positive-cpu", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    build_google_event_summary(
        input_path=args.input,
        output_path=args.output,
        time_column=args.time_column,
        cpu_column=args.cpu_column,
        priority_column=args.priority_column,
        latency_column=args.latency_column,
        tick_seconds=args.tick_seconds,
        epoch_start_us=args.epoch_start_us,
        cpu_already_normalized=args.cpu_already_normalized,
        cpu_normalization_max=args.cpu_normalization_max,
        priority_high_threshold=args.priority_high_threshold,
        default_duration_p50=args.default_duration_p50,
        default_duration_p90=args.default_duration_p90,
        skip_non_positive_cpu=args.skip_non_positive_cpu,
    )


if __name__ == "__main__":
    main()

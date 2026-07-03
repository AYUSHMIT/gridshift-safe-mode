"""Build GridShift's derived 5-minute ClusterData workload summary.

This script consumes a local CSV produced by Google ClusterData2019
preprocessing. It does not parse raw protobuf/event files. The input is
expected to contain one row per runnable task/job attempt with at least:

    start time, runtime or end time, and CPU demand

Optional priority and latency columns are used to estimate GridShift priority
fractions. The output schema is exactly:

    tick,arrivals,cpu_demand_norm,duration_p50,duration_p90,
    priority_high_frac,latency_sensitive_frac

Assumptions and limitations:
  - Aggregation uses the row start time as the arrival time.
  - The first observed start time is tick zero unless --epoch-start is set.
  - Runtimes are converted to simulator ticks by dividing by --tick-seconds.
  - Zero-length runtimes are handled by flooring to --min-duration-ticks.
  - CPU demand is normalized globally by --cpu-normalization-max, or by the
    maximum observed value if no normalization max is provided. If the input
    CPU column is already normalized, pass --cpu-already-normalized.
  - Priority/latency fractions are heuristics over the preprocessed rows and
    must be documented with the BigQuery query used for paper-grade runs.

Example:
    python -m experiments.build_google_cluster_summary \
        --input /path/to/cluster_tasks_preprocessed.csv \
        --output data/traces/google_cluster_derived_5min.csv \
        --time-column start_time \
        --end-time-column end_time \
        --time-unit micros \
        --cpu-column cpu_request \
        --priority-column priority \
        --priority-high-threshold 9 \
        --latency-column latency_sensitive
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


@dataclass(frozen=True)
class TaskRow:
    start_seconds: float
    duration_ticks: float
    cpu_demand: float
    high_priority: bool
    latency_sensitive: bool


@dataclass
class TickBucket:
    arrivals: int
    cpu_values: list[float]
    durations: list[float]
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


def _scale_time(value: float, unit: str) -> float:
    if unit == "seconds":
        return value
    if unit == "millis":
        return value / 1_000.0
    if unit == "micros":
        return value / 1_000_000.0
    if unit == "nanos":
        return value / 1_000_000_000.0
    raise ValueError(f"Unsupported time unit: {unit}")


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


def _row_bool(row: dict[str, str], column: str | None) -> bool:
    if column is None:
        return False
    return str(row.get(column, "")).strip().lower() in TRUTHY


def _load_rows(
    input_path: Path,
    *,
    time_column: str,
    end_time_column: str | None,
    duration_column: str | None,
    cpu_column: str,
    priority_column: str | None,
    priority_high_threshold: float,
    latency_column: str | None,
    time_unit: str,
    duration_unit: str,
    tick_seconds: float,
    min_duration_ticks: float,
    skip_non_positive_cpu: bool,
) -> list[TaskRow]:
    rows: list[TaskRow] = []

    with input_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"Input CSV is empty: {input_path}")

        required = {time_column, cpu_column}
        if duration_column is None and end_time_column is None:
            raise ValueError("Provide either --duration-column or --end-time-column")
        if duration_column is not None:
            required.add(duration_column)
        elif end_time_column is not None:
            required.add(end_time_column)
        if priority_column is not None:
            required.add(priority_column)
        if latency_column is not None:
            required.add(latency_column)

        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(f"Input CSV is missing columns: {sorted(missing)}")

        for row in reader:
            start_raw = _parse_float(row, time_column, input_path)
            start_seconds = _scale_time(start_raw, time_unit)

            if duration_column is not None:
                duration_raw = _parse_float(row, duration_column, input_path)
                duration_seconds = _scale_time(duration_raw, duration_unit)
            else:
                end_raw = _parse_float(row, end_time_column or "", input_path)
                end_seconds = _scale_time(end_raw, time_unit)
                duration_seconds = end_seconds - start_seconds

            duration_ticks = duration_seconds / tick_seconds
            duration_ticks = max(min_duration_ticks, duration_ticks)

            cpu = _parse_float(row, cpu_column, input_path)
            if cpu < 0:
                raise ValueError(f"Negative CPU demand in {input_path}: {cpu}")
            if cpu == 0 and skip_non_positive_cpu:
                continue

            high_priority = False
            if priority_column is not None:
                priority = _parse_float(row, priority_column, input_path)
                high_priority = priority >= priority_high_threshold

            rows.append(
                TaskRow(
                    start_seconds=start_seconds,
                    duration_ticks=duration_ticks,
                    cpu_demand=cpu,
                    high_priority=high_priority,
                    latency_sensitive=_row_bool(row, latency_column),
                )
            )

    if not rows:
        raise ValueError(f"No usable task rows found in {input_path}")

    return rows


def build_google_cluster_summary(
    *,
    input_path: str,
    output_path: str,
    time_column: str = "start_time",
    end_time_column: str | None = "end_time",
    duration_column: str | None = None,
    cpu_column: str = "cpu_request",
    priority_column: str | None = "priority",
    priority_high_threshold: float = 9.0,
    latency_column: str | None = "latency_sensitive",
    time_unit: str = "micros",
    duration_unit: str = "seconds",
    tick_seconds: float = 300.0,
    epoch_start: float | None = None,
    cpu_already_normalized: bool = False,
    cpu_normalization_max: float | None = None,
    min_duration_ticks: float = 1.0,
    skip_non_positive_cpu: bool = False,
) -> None:
    """Aggregate a preprocessed ClusterData CSV into GridShift trace format."""
    source = Path(input_path)
    if not source.exists():
        raise FileNotFoundError(f"Input CSV not found: {source}")
    if tick_seconds <= 0:
        raise ValueError("--tick-seconds must be positive")
    if min_duration_ticks <= 0:
        raise ValueError("--min-duration-ticks must be positive")

    rows = _load_rows(
        source,
        time_column=time_column,
        end_time_column=end_time_column,
        duration_column=duration_column,
        cpu_column=cpu_column,
        priority_column=priority_column,
        priority_high_threshold=priority_high_threshold,
        latency_column=latency_column,
        time_unit=time_unit,
        duration_unit=duration_unit,
        tick_seconds=tick_seconds,
        min_duration_ticks=min_duration_ticks,
        skip_non_positive_cpu=skip_non_positive_cpu,
    )

    origin = epoch_start
    if origin is None:
        origin = min(row.start_seconds for row in rows)
    else:
        origin = _scale_time(origin, time_unit)

    cpu_max = cpu_normalization_max
    if cpu_already_normalized:
        cpu_max = 1.0
    elif cpu_max is None:
        cpu_max = max(row.cpu_demand for row in rows)
    if cpu_max <= 0:
        raise ValueError("CPU normalization max must be positive")

    buckets: dict[int, TickBucket] = {}
    for row in rows:
        tick = int(math.floor((row.start_seconds - origin) / tick_seconds))
        if tick < 0:
            continue
        bucket = buckets.setdefault(
            tick,
            TickBucket(
                arrivals=0,
                cpu_values=[],
                durations=[],
                high_priority=0,
                latency_sensitive=0,
            ),
        )
        bucket.arrivals += 1
        bucket.cpu_values.append(min(1.0, max(0.0, row.cpu_demand / cpu_max)))
        bucket.durations.append(row.duration_ticks)
        bucket.high_priority += int(row.high_priority)
        bucket.latency_sensitive += int(row.latency_sensitive)

    if not buckets:
        raise ValueError("No rows fell within non-negative simulation ticks")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DERIVED_FIELDNAMES)
        writer.writeheader()
        for tick in sorted(buckets):
            bucket = buckets[tick]
            arrivals = bucket.arrivals
            cpu_mean = sum(bucket.cpu_values) / arrivals
            duration_p50 = _percentile(bucket.durations, 0.50)
            duration_p90 = _percentile(bucket.durations, 0.90)
            writer.writerow(
                {
                    "tick": tick,
                    "arrivals": arrivals,
                    "cpu_demand_norm": round(cpu_mean, 6),
                    "duration_p50": round(duration_p50, 6),
                    "duration_p90": round(duration_p90, 6),
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
    print("validated_schema:", ",".join(DERIVED_FIELDNAMES))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate a preprocessed Google ClusterData2019 CSV into "
            "GridShift's derived 5-minute workload summary."
        )
    )
    parser.add_argument("--input", required=True, help="Preprocessed ClusterData CSV.")
    parser.add_argument(
        "--output",
        default="data/traces/google_cluster_derived_5min.csv",
        help="Derived GridShift trace CSV to write.",
    )
    parser.add_argument("--time-column", default="start_time")
    parser.add_argument("--end-time-column", default="end_time")
    parser.add_argument("--duration-column", default=None)
    parser.add_argument("--cpu-column", default="cpu_request")
    parser.add_argument("--priority-column", default="priority")
    parser.add_argument("--priority-high-threshold", type=float, default=9.0)
    parser.add_argument("--latency-column", default="latency_sensitive")
    parser.add_argument(
        "--time-unit",
        choices=["seconds", "millis", "micros", "nanos"],
        default="micros",
        help="Unit for start/end timestamps.",
    )
    parser.add_argument(
        "--duration-unit",
        choices=["seconds", "millis", "micros", "nanos"],
        default="seconds",
        help="Unit for --duration-column, when supplied.",
    )
    parser.add_argument("--tick-seconds", type=float, default=300.0)
    parser.add_argument(
        "--epoch-start",
        type=float,
        default=None,
        help="Optional absolute start timestamp for tick zero, in --time-unit.",
    )
    parser.add_argument("--cpu-already-normalized", action="store_true")
    parser.add_argument("--cpu-normalization-max", type=float, default=None)
    parser.add_argument("--min-duration-ticks", type=float, default=1.0)
    parser.add_argument("--skip-non-positive-cpu", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    end_time_column = args.end_time_column or None
    priority_column = args.priority_column or None
    latency_column = args.latency_column or None
    build_google_cluster_summary(
        input_path=args.input,
        output_path=args.output,
        time_column=args.time_column,
        end_time_column=end_time_column,
        duration_column=args.duration_column,
        cpu_column=args.cpu_column,
        priority_column=priority_column,
        priority_high_threshold=args.priority_high_threshold,
        latency_column=latency_column,
        time_unit=args.time_unit,
        duration_unit=args.duration_unit,
        tick_seconds=args.tick_seconds,
        epoch_start=args.epoch_start,
        cpu_already_normalized=args.cpu_already_normalized,
        cpu_normalization_max=args.cpu_normalization_max,
        min_duration_ticks=args.min_duration_ticks,
        skip_non_positive_cpu=args.skip_non_positive_cpu,
    )


if __name__ == "__main__":
    main()

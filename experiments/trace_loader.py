"""Helpers for loading and calibrating normalized power traces.

The trace file is treated as a calibrated DC power profile. The experiment
layer can rescale a raw Google PowerData signal onto a local GridShift
reference range without changing orchestration or grid code.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PowerTracePoint:
    tick: int
    power_mw: float


@dataclass(frozen=True)
class DerivedTracePoint:
    tick: int
    arrivals: float
    cpu_demand_norm: float
    duration_p50: float
    duration_p90: float
    priority_high_frac: float
    latency_sensitive_frac: float


def load_power_trace(path: str | Path) -> list[PowerTracePoint]:
    """Load a normalized power trace CSV.

    Expected schema:
        tick,power_mw
    """
    trace_path = Path(path)

    if not trace_path.exists():
        raise FileNotFoundError(f"Trace file not found: {trace_path}")

    points: list[PowerTracePoint] = []

    with trace_path.open(newline="") as f:
        reader = csv.DictReader(f)

        required = {"tick", "power_mw"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"Trace file {trace_path} is missing columns: {sorted(missing)}"
            )

        for row in reader:
            points.append(
                PowerTracePoint(
                    tick=int(row["tick"]),
                    power_mw=float(row["power_mw"]),
                )
            )

    if not points:
        raise ValueError(f"Trace file is empty: {trace_path}")

    return sorted(points, key=lambda p: p.tick)


def load_derived_trace(path: str | Path) -> list[DerivedTracePoint]:
    """Load a derived ClusterData-style 5-minute workload summary.

    Expected schema:
        tick,arrivals,cpu_demand_norm,duration_p50,duration_p90,
        priority_high_frac,latency_sensitive_frac
    """
    trace_path = Path(path)

    if not trace_path.exists():
        raise FileNotFoundError(f"Trace file not found: {trace_path}")

    required = {
        "tick",
        "arrivals",
        "cpu_demand_norm",
        "duration_p50",
        "duration_p90",
        "priority_high_frac",
        "latency_sensitive_frac",
    }
    points: list[DerivedTracePoint] = []

    with trace_path.open(newline="") as f:
        reader = csv.DictReader(f)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"Trace file {trace_path} is missing columns: {sorted(missing)}"
            )

        seen_ticks = set()
        for row in reader:
            point = DerivedTracePoint(
                tick=int(row["tick"]),
                arrivals=float(row["arrivals"]),
                cpu_demand_norm=float(row["cpu_demand_norm"]),
                duration_p50=float(row["duration_p50"]),
                duration_p90=float(row["duration_p90"]),
                priority_high_frac=float(row["priority_high_frac"]),
                latency_sensitive_frac=float(row["latency_sensitive_frac"]),
            )
            _validate_derived_point(point, trace_path)
            if point.tick in seen_ticks:
                raise ValueError(f"Duplicate tick {point.tick} in {trace_path}")
            seen_ticks.add(point.tick)
            points.append(point)

    if not points:
        raise ValueError(f"Trace file is empty: {trace_path}")

    return sorted(points, key=lambda p: p.tick)


def _validate_derived_point(point: DerivedTracePoint, trace_path: Path) -> None:
    if point.tick < 0:
        raise ValueError(f"Negative tick {point.tick} in {trace_path}")
    if point.arrivals < 0:
        raise ValueError(f"Negative arrivals at tick {point.tick} in {trace_path}")
    if point.cpu_demand_norm < 0:
        raise ValueError(
            f"Negative cpu_demand_norm at tick {point.tick} in {trace_path}"
        )
    if point.duration_p50 <= 0 or point.duration_p90 <= 0:
        raise ValueError(
            f"Non-positive duration at tick {point.tick} in {trace_path}"
        )
    if point.duration_p90 < point.duration_p50:
        raise ValueError(
            f"duration_p90 < duration_p50 at tick {point.tick} in {trace_path}"
        )
    for name, value in (
        ("priority_high_frac", point.priority_high_frac),
        ("latency_sensitive_frac", point.latency_sensitive_frac),
    ):
        if value < 0 or value > 1:
            raise ValueError(
                f"{name}={value} outside [0, 1] at tick {point.tick} "
                f"in {trace_path}"
            )


def power_at_tick(points: list[PowerTracePoint], tick: int) -> float:
    """Return the latest trace power value at or before the current tick."""
    current = points[0].power_mw

    for point in points:
        if point.tick > tick:
            break
        current = point.power_mw

    return current


def derived_at_tick(
    points: list[DerivedTracePoint],
    tick: int,
) -> DerivedTracePoint | None:
    """Return the derived row for this exact simulation tick, if present."""
    for point in points:
        if point.tick == tick:
            return point
        if point.tick > tick:
            break
    return None


def power_bounds(points: list[PowerTracePoint]) -> tuple[float, float]:
    """Return the inclusive power range of a trace."""
    values = [point.power_mw for point in points]
    if not values:
        raise ValueError("Cannot compute power bounds for an empty trace")
    return min(values), max(values)


def rescale_trace_points(
    points: list[PowerTracePoint],
    *,
    target_min_mw: float,
    target_max_mw: float,
) -> list[PowerTracePoint]:
    """Map a trace onto a target power range while preserving shape.

    The source trace is min-max normalized first, then mapped to the target
    workload envelope. This preserves the temporal pattern while changing only
    the amplitude.
    """
    if target_max_mw < target_min_mw:
        raise ValueError("target_max_mw must be greater than or equal to target_min_mw")

    source_min, source_max = power_bounds(points)
    if source_max == source_min:
        midpoint = (target_min_mw + target_max_mw) / 2.0
        return [PowerTracePoint(tick=point.tick, power_mw=midpoint) for point in points]

    target_span = target_max_mw - target_min_mw
    source_span = source_max - source_min

    return [
        PowerTracePoint(
            tick=point.tick,
            power_mw=target_min_mw
            + ((point.power_mw - source_min) / source_span) * target_span,
        )
        for point in points
    ]

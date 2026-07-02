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


def power_at_tick(points: list[PowerTracePoint], tick: int) -> float:
    """Return the latest trace power value at or before the current tick."""
    current = points[0].power_mw

    for point in points:
        if point.tick > tick:
            break
        current = point.power_mw

    return current


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
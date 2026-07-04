"""Load derived regional grid traces for GridShift experiments."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


REQUIRED_COLUMNS = {
    "tick",
    "timestamp_utc",
    "gridshift_region",
    "iso_ne_zone",
    "load_mw",
}


@dataclass(frozen=True)
class GridTracePoint:
    tick: int
    timestamp_utc: str
    gridshift_region: str
    iso_ne_zone: str
    load_mw: float


def load_grid_trace(path: str | Path) -> list[GridTracePoint]:
    """Load a strict 5-minute regional grid trace CSV."""
    trace_path = Path(path)
    if not trace_path.exists():
        raise FileNotFoundError(f"Grid trace file not found: {trace_path}")

    points: list[GridTracePoint] = []
    seen: set[tuple[int, str]] = set()

    with trace_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"Grid trace {trace_path} is missing columns: {sorted(missing)}"
            )

        for rownum, row in enumerate(reader, start=2):
            try:
                tick = int(row["tick"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"{trace_path}:{rownum}: invalid tick {row.get('tick')!r}"
                ) from exc

            try:
                load_mw = float(row["load_mw"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"{trace_path}:{rownum}: invalid load_mw "
                    f"{row.get('load_mw')!r}"
                ) from exc

            if tick < 0:
                raise ValueError(f"{trace_path}:{rownum}: negative tick {tick}")
            if load_mw < 0:
                raise ValueError(
                    f"{trace_path}:{rownum}: negative load_mw {load_mw}"
                )

            timestamp_utc = row["timestamp_utc"].strip()
            gridshift_region = row["gridshift_region"].strip()
            iso_ne_zone = row["iso_ne_zone"].strip()
            if not timestamp_utc:
                raise ValueError(f"{trace_path}:{rownum}: empty timestamp_utc")
            if not gridshift_region:
                raise ValueError(f"{trace_path}:{rownum}: empty gridshift_region")
            if not iso_ne_zone:
                raise ValueError(f"{trace_path}:{rownum}: empty iso_ne_zone")

            key = (tick, gridshift_region)
            if key in seen:
                raise ValueError(
                    f"{trace_path}:{rownum}: duplicate tick+region row {key}"
                )
            seen.add(key)

            points.append(
                GridTracePoint(
                    tick=tick,
                    timestamp_utc=timestamp_utc,
                    gridshift_region=gridshift_region,
                    iso_ne_zone=iso_ne_zone,
                    load_mw=load_mw,
                )
            )

    if not points:
        raise ValueError(f"Grid trace file is empty: {trace_path}")

    return sorted(points, key=lambda point: (point.tick, point.gridshift_region))

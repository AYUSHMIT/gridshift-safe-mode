"""Build a GridShift grid baseline trace from ISO-NE zonal load exports.

This script expects a CSV exported from ISO-NE Five-Minute Estimated Zonal Load
or an equivalent authoritative zonal-load source. Column names are deliberately
provided by the caller because ISO-NE report headers can vary by access path.

The output schema is:

    tick,timestamp_utc,gridshift_region,iso_ne_zone,load_mw

No overloads are injected and no thresholds are derived here.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from experiments.grid_trace_loader import load_grid_trace


FIELDNAMES = [
    "tick",
    "timestamp_utc",
    "gridshift_region",
    "iso_ne_zone",
    "load_mw",
]

MAPPING_COLUMNS = {
    "gridshift_region",
    "iso_ne_zone",
    "aggregation_weight",
    "rationale",
}


@dataclass(frozen=True)
class ZoneMapping:
    gridshift_region: str
    iso_ne_zone: str
    aggregation_weight: float
    rationale: str


@dataclass(frozen=True)
class SourcePoint:
    timestamp_utc: datetime
    iso_ne_zone: str
    load_mw: float


def _parse_timestamp(value: str, source_timezone: ZoneInfo) -> datetime:
    text = value.strip()
    if not text:
        raise ValueError("empty timestamp")
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"could not parse timestamp {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=source_timezone)
    return dt.astimezone(timezone.utc)


def _load_mapping(path: Path) -> list[ZoneMapping]:
    if not path.exists():
        raise FileNotFoundError(f"Zone mapping CSV not found: {path}")

    mappings: list[ZoneMapping] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = MAPPING_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Mapping CSV {path} is missing columns: {sorted(missing)}")
        for rownum, row in enumerate(reader, start=2):
            region = row["gridshift_region"].strip()
            zone = row["iso_ne_zone"].strip()
            rationale = row["rationale"].strip()
            if not region:
                raise ValueError(f"{path}:{rownum}: empty gridshift_region")
            if not zone:
                raise ValueError(f"{path}:{rownum}: empty iso_ne_zone")
            if not rationale:
                raise ValueError(f"{path}:{rownum}: empty rationale")
            try:
                weight = float(row["aggregation_weight"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"{path}:{rownum}: invalid aggregation_weight "
                    f"{row.get('aggregation_weight')!r}"
                ) from exc
            if weight < 0:
                raise ValueError(f"{path}:{rownum}: negative aggregation_weight")
            mappings.append(
                ZoneMapping(
                    gridshift_region=region,
                    iso_ne_zone=zone,
                    aggregation_weight=weight,
                    rationale=rationale,
                )
            )
    if not mappings:
        raise ValueError(f"Mapping CSV is empty: {path}")
    return mappings


def _load_source_points(
    path: Path,
    *,
    timestamp_column: str,
    zone_column: str,
    load_column: str,
    source_timezone: ZoneInfo,
) -> list[SourcePoint]:
    if not path.exists():
        raise FileNotFoundError(f"Input CSV not found: {path}")

    points: list[SourcePoint] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {timestamp_column, zone_column, load_column}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Input CSV {path} is missing columns: {sorted(missing)}")

        for rownum, row in enumerate(reader, start=2):
            try:
                timestamp = _parse_timestamp(row[timestamp_column], source_timezone)
            except ValueError as exc:
                raise ValueError(f"{path}:{rownum}: {exc}") from exc

            zone = row[zone_column].strip()
            if not zone:
                raise ValueError(f"{path}:{rownum}: empty zone")

            try:
                load_mw = float(row[load_column])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"{path}:{rownum}: invalid load value {row.get(load_column)!r}"
                ) from exc
            if load_mw < 0:
                raise ValueError(f"{path}:{rownum}: negative load_mw {load_mw}")

            points.append(
                SourcePoint(
                    timestamp_utc=timestamp,
                    iso_ne_zone=zone,
                    load_mw=load_mw,
                )
            )

    if not points:
        raise ValueError(f"Input CSV is empty: {path}")
    return sorted(points, key=lambda point: (point.iso_ne_zone, point.timestamp_utc))


def _require_or_resample_five_minute(
    points: list[SourcePoint],
    *,
    resample: str,
) -> list[SourcePoint]:
    by_zone: dict[str, list[SourcePoint]] = {}
    for point in points:
        by_zone.setdefault(point.iso_ne_zone, []).append(point)

    five_minutes = timedelta(minutes=5)
    needs_resample = False
    for zone_points in by_zone.values():
        unique_times = sorted({point.timestamp_utc for point in zone_points})
        for earlier, later in zip(unique_times, unique_times[1:]):
            if later - earlier != five_minutes:
                needs_resample = True
                break
        if needs_resample:
            break

    if not needs_resample:
        return points
    if resample == "none":
        raise ValueError(
            "Input is not at strict 5-minute resolution; pass --resample linear "
            "to explicitly resample."
        )
    if resample != "linear":
        raise ValueError(f"Unsupported resample mode: {resample}")

    resampled: list[SourcePoint] = []
    for zone, zone_points in by_zone.items():
        collapsed = _collapse_duplicate_zone_times(zone_points)
        times = sorted(collapsed)
        current = times[0]
        end = times[-1]
        while current <= end:
            resampled.append(
                SourcePoint(
                    timestamp_utc=current,
                    iso_ne_zone=zone,
                    load_mw=_interpolated_load(collapsed, times, current),
                )
            )
            current += five_minutes
    return sorted(resampled, key=lambda point: (point.iso_ne_zone, point.timestamp_utc))


def _collapse_duplicate_zone_times(points: list[SourcePoint]) -> dict[datetime, float]:
    grouped: dict[datetime, list[float]] = {}
    for point in points:
        grouped.setdefault(point.timestamp_utc, []).append(point.load_mw)
    return {
        timestamp: sum(values) / len(values)
        for timestamp, values in grouped.items()
    }


def _interpolated_load(
    loads_by_time: dict[datetime, float],
    times: list[datetime],
    timestamp: datetime,
) -> float:
    if timestamp in loads_by_time:
        return loads_by_time[timestamp]
    earlier = max(time for time in times if time < timestamp)
    later = min(time for time in times if time > timestamp)
    span = (later - earlier).total_seconds()
    offset = (timestamp - earlier).total_seconds()
    weight = offset / span
    return loads_by_time[earlier] * (1.0 - weight) + loads_by_time[later] * weight


def _timestamp_label(timestamp: datetime) -> str:
    return timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_iso_ne_grid_summary(
    *,
    input_path: str,
    mapping_path: str,
    output_path: str,
    timestamp_column: str,
    zone_column: str,
    load_column: str,
    source_timezone: str,
    resample: str = "none",
    start_tick: int = 1,
) -> None:
    """Convert ISO-NE zonal load rows into GridShift's grid trace schema."""
    if start_tick < 0:
        raise ValueError("--start-tick must be non-negative")
    tz = ZoneInfo(source_timezone)
    mappings = _load_mapping(Path(mapping_path))
    points = _load_source_points(
        Path(input_path),
        timestamp_column=timestamp_column,
        zone_column=zone_column,
        load_column=load_column,
        source_timezone=tz,
    )
    points = _require_or_resample_five_minute(points, resample=resample)

    mappings_by_zone: dict[str, list[ZoneMapping]] = {}
    for mapping in mappings:
        mappings_by_zone.setdefault(mapping.iso_ne_zone, []).append(mapping)

    aggregated: dict[tuple[datetime, str], dict[str, object]] = {}
    ignored_zones: set[str] = set()
    for point in points:
        zone_mappings = mappings_by_zone.get(point.iso_ne_zone)
        if not zone_mappings:
            ignored_zones.add(point.iso_ne_zone)
            continue
        for mapping in zone_mappings:
            key = (point.timestamp_utc, mapping.gridshift_region)
            row = aggregated.setdefault(
                key,
                {
                    "load_mw": 0.0,
                    "zones": set(),
                },
            )
            row["load_mw"] = float(row["load_mw"]) + (
                point.load_mw * mapping.aggregation_weight
            )
            row["zones"].add(point.iso_ne_zone)

    if not aggregated:
        raise ValueError("No source rows matched the zone mapping CSV")

    timestamps = sorted({timestamp for timestamp, _region in aggregated})
    tick_by_timestamp = {
        timestamp: start_tick + idx
        for idx, timestamp in enumerate(timestamps)
    }

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=FIELDNAMES,
            lineterminator="\n",
        )
        writer.writeheader()
        for timestamp, region in sorted(aggregated):
            row = aggregated[(timestamp, region)]
            zones = "+".join(sorted(row["zones"]))
            writer.writerow(
                {
                    "tick": tick_by_timestamp[timestamp],
                    "timestamp_utc": _timestamp_label(timestamp),
                    "gridshift_region": region,
                    "iso_ne_zone": zones,
                    "load_mw": round(float(row["load_mw"]), 6),
                }
            )

    loaded = load_grid_trace(output)
    print("wrote:", output)
    print("rows:", len(loaded))
    print("tick_range:", f"{loaded[0].tick}..{loaded[-1].tick}")
    if ignored_zones:
        print("ignored_unmapped_zones:", ",".join(sorted(ignored_zones)))
    print("resample:", resample)
    print("validated_schema:", ",".join(FIELDNAMES))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a GridShift grid trace from ISO-NE Five-Minute Estimated "
            "Zonal Load CSV exports."
        )
    )
    parser.add_argument("--input", required=True, help="ISO-NE zonal load CSV.")
    parser.add_argument("--mapping", required=True, help="Explicit zone mapping CSV.")
    parser.add_argument(
        "--output",
        default="data/grid/iso_ne_grid_derived_5min.csv",
        help="Derived GridShift grid trace CSV to write.",
    )
    parser.add_argument("--timestamp-column", required=True)
    parser.add_argument("--zone-column", required=True)
    parser.add_argument("--load-column", required=True)
    parser.add_argument(
        "--source-timezone",
        required=True,
        help="IANA timezone for naive source timestamps, e.g. America/New_York.",
    )
    parser.add_argument(
        "--resample",
        choices=["none", "linear"],
        default="none",
        help="Explicit resampling mode for non-5-minute source intervals.",
    )
    parser.add_argument("--start-tick", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    build_iso_ne_grid_summary(
        input_path=args.input,
        mapping_path=args.mapping,
        output_path=args.output,
        timestamp_column=args.timestamp_column,
        zone_column=args.zone_column,
        load_column=args.load_column,
        source_timezone=args.source_timezone,
        resample=args.resample,
        start_tick=args.start_tick,
    )


if __name__ == "__main__":
    main()

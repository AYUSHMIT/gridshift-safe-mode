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
import base64
import csv
import json
import os
import urllib.request
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

ISONE_ENDPOINT = (
    "https://webservices.iso-ne.com/api/v1.1/"
    "fiveminuteestimatedzonalload/day/{day}"
)

MASSACHUSETTS_ZONE_IDS = {
    "4006": ("SEMASS", ".Z.SEMASS"),
    "4007": ("WCMASS", ".Z.WCMASS"),
    "4008": ("NEMASSBOST", ".Z.NEMASSBOST"),
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
    estimated_btm_pv_mw: float | None = None
    gridshift_region: str | None = None


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


def _fetch_iso_ne_day(day: str) -> dict:
    username = os.environ.get("ISONE_USERNAME")
    password = os.environ.get("ISONE_PASSWORD")
    if not username or not password:
        raise ValueError(
            "Set ISONE_USERNAME and ISONE_PASSWORD to fetch ISO-NE data"
        )
    if len(day) != 8 or not day.isdigit():
        raise ValueError("--fetch-day must use YYYYMMDD format")

    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode(
        "ascii"
    )
    request = urllib.request.Request(
        ISONE_ENDPOINT.format(day=day),
        headers={
            "Accept": "application/json",
            "Authorization": f"Basic {token}",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _load_json_payload(path: str | None, fetch_day: str | None) -> dict:
    if fetch_day is not None:
        return _fetch_iso_ne_day(fetch_day)
    if path is None:
        raise ValueError("Provide --input, --json-input, or --fetch-day")
    json_path = Path(path)
    if not json_path.exists():
        raise FileNotFoundError(f"JSON input not found: {json_path}")
    with json_path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _records_from_iso_ne_payload(payload: dict) -> list[dict]:
    try:
        records = payload["isone_web_services"][
            "five_min_estimated_zonal_loads"
        ]["five_min_estimated_zonal_load"]
    except KeyError as exc:
        raise ValueError("JSON payload does not match ISO-NE zonal load shape") from exc
    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list) or not records:
        raise ValueError("ISO-NE JSON payload contains no zonal load records")
    return records


def _load_iso_ne_json_points(payload: dict) -> list[SourcePoint]:
    points: list[SourcePoint] = []
    for idx, record in enumerate(_records_from_iso_ne_payload(payload), start=1):
        missing = {
            "interval_begin_date",
            "load_zone_id",
            "load_zone_name",
            "estimated_load_mw",
            "estimated_btm_pv_mw",
        } - set(record)
        if missing:
            raise ValueError(f"ISO-NE JSON record {idx} missing fields: {sorted(missing)}")

        zone_id = str(record["load_zone_id"])
        if zone_id not in MASSACHUSETTS_ZONE_IDS:
            continue
        gridshift_region, expected_zone_name = MASSACHUSETTS_ZONE_IDS[zone_id]
        zone_name = str(record["load_zone_name"]).strip()
        if zone_name != expected_zone_name:
            raise ValueError(
                f"ISO-NE JSON record {idx}: load_zone_id {zone_id} expected "
                f"{expected_zone_name}, found {zone_name}"
            )

        try:
            load_mw = float(record["estimated_load_mw"])
            btm_pv_mw = float(record["estimated_btm_pv_mw"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"ISO-NE JSON record {idx}: invalid MW value") from exc
        if load_mw < 0 or btm_pv_mw < 0:
            raise ValueError(f"ISO-NE JSON record {idx}: negative MW value")

        try:
            timestamp_utc = _parse_timestamp(
                str(record["interval_begin_date"]),
                ZoneInfo("America/New_York"),
            )
        except ValueError as exc:
            raise ValueError(f"ISO-NE JSON record {idx}: {exc}") from exc

        points.append(
            SourcePoint(
                timestamp_utc=timestamp_utc,
                iso_ne_zone=zone_name,
                load_mw=load_mw,
                estimated_btm_pv_mw=btm_pv_mw,
                gridshift_region=gridshift_region,
            )
        )

    if not points:
        raise ValueError("ISO-NE JSON payload has no selected Massachusetts zones")
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
    input_path: str | None,
    json_input_path: str | None,
    fetch_day: str | None,
    mapping_path: str | None,
    output_path: str,
    timestamp_column: str | None,
    zone_column: str | None,
    load_column: str | None,
    source_timezone: str | None,
    resample: str = "none",
    start_tick: int = 1,
) -> None:
    """Convert ISO-NE zonal load rows into GridShift's grid trace schema."""
    if start_tick < 0:
        raise ValueError("--start-tick must be non-negative")
    source_modes = sum(
        value is not None
        for value in (input_path, json_input_path, fetch_day)
    )
    if source_modes != 1:
        raise ValueError("Provide exactly one of --input, --json-input, or --fetch-day")

    if json_input_path is not None or fetch_day is not None:
        payload = _load_json_payload(json_input_path, fetch_day)
        points = _load_iso_ne_json_points(payload)
        points = _require_or_resample_five_minute(points, resample=resample)
        aggregated, ignored_zones = _aggregate_json_points(points)
    else:
        if mapping_path is None:
            raise ValueError("--mapping is required for CSV input")
        if timestamp_column is None or zone_column is None or load_column is None:
            raise ValueError(
                "--timestamp-column, --zone-column, and --load-column are "
                "required for CSV input"
            )
        if source_timezone is None:
            raise ValueError("--source-timezone is required for CSV input")
        tz = ZoneInfo(source_timezone)
        mappings = _load_mapping(Path(mapping_path))
        points = _load_source_points(
            Path(input_path or ""),
            timestamp_column=timestamp_column,
            zone_column=zone_column,
            load_column=load_column,
            source_timezone=tz,
        )
        points = _require_or_resample_five_minute(points, resample=resample)
        aggregated, ignored_zones = _aggregate_csv_points(points, mappings)

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


def _aggregate_csv_points(
    points: list[SourcePoint],
    mappings: list[ZoneMapping],
) -> tuple[dict[tuple[datetime, str], dict[str, object]], set[str]]:
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
            row = aggregated.setdefault(key, {"load_mw": 0.0, "zones": set()})
            row["load_mw"] = float(row["load_mw"]) + (
                point.load_mw * mapping.aggregation_weight
            )
            row["zones"].add(point.iso_ne_zone)
    return aggregated, ignored_zones


def _aggregate_json_points(
    points: list[SourcePoint],
) -> tuple[dict[tuple[datetime, str], dict[str, object]], set[str]]:
    aggregated: dict[tuple[datetime, str], dict[str, object]] = {}
    for point in points:
        if point.gridshift_region is None:
            raise ValueError("JSON source point missing GridShift region")
        key = (point.timestamp_utc, point.gridshift_region)
        if key in aggregated:
            raise ValueError(f"Duplicate ISO-NE JSON row for {key}")
        aggregated[key] = {
            "load_mw": point.load_mw,
            "zones": {point.iso_ne_zone},
        }
    return aggregated, set()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a GridShift grid trace from ISO-NE Five-Minute Estimated "
            "Zonal Load CSV exports or JSON Web Services responses."
        )
    )
    parser.add_argument("--input", default=None, help="ISO-NE zonal load CSV.")
    parser.add_argument(
        "--json-input",
        default=None,
        help="Saved ISO-NE Web Services JSON response.",
    )
    parser.add_argument(
        "--fetch-day",
        default=None,
        help=(
            "Fetch /fiveminuteestimatedzonalload/day/{YYYYMMDD}; "
            "requires ISONE_USERNAME and ISONE_PASSWORD."
        ),
    )
    parser.add_argument(
        "--mapping",
        default=None,
        help="Explicit zone mapping CSV. Required for CSV input.",
    )
    parser.add_argument(
        "--output",
        default="data/grid/iso_ne_grid_derived_5min.csv",
        help="Derived GridShift grid trace CSV to write.",
    )
    parser.add_argument("--timestamp-column", default=None)
    parser.add_argument("--zone-column", default=None)
    parser.add_argument("--load-column", default=None)
    parser.add_argument(
        "--source-timezone",
        default=None,
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
        json_input_path=args.json_input,
        fetch_day=args.fetch_day,
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

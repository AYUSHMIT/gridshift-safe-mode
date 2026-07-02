"""Convert a local Google PowerData 2019 export into GridShift trace format.

Google publishes PowerData 2019 through BigQuery and a Colab notebook. The raw
tables expose time-aligned power-utilization measurements rather than a
pre-normalized GridShift trace, so this converter keeps the raw data local and
configurable.

Expected normalized output:
    tick,power_mw

Calibration workflow:
    1. Load the raw Google utilization or power series.
    2. Preserve its temporal shape by min-max normalizing it.
    3. Rescale it to the power range of a local GridShift reference trace.

The script never fabricates a missing conversion factor. For calibration mode,
utilization-only exports are accepted directly and normalized against the
reference trace range. Capacity metadata is optional and only useful if you
want to precompute an MW series before normalization.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Iterable

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from experiments.trace_loader import PowerTracePoint, load_power_trace, rescale_trace_points


def _existing_csv_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(sorted(p for p in path.glob("*.csv") if p.is_file()))
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(f"Input path not found: {path}")

    if not files:
        raise FileNotFoundError("No CSV inputs were found")

    return files


def _parse_float(value: str, *, field: str, source: Path) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Could not parse {field!r} from {source}: {value!r}"
        ) from exc


def _row_is_bad(row: dict[str, str], *, skip_bad_measurements: bool, skip_bad_production: bool) -> bool:
    if skip_bad_measurements and row.get("bad_measurement_data", "").lower() in {"true", "1", "yes"}:
        return True
    if skip_bad_production and row.get("bad_production_power_data", "").lower() in {"true", "1", "yes"}:
        return True
    return False


def _row_power_mw(
    row: dict[str, str],
    *,
    source: Path,
    power_column: str | None,
    utilization_column: str | None,
) -> float:
    if power_column is not None:
        if power_column not in row:
            raise ValueError(f"Missing power column {power_column!r} in {source}")
        return _parse_float(row[power_column], field=power_column, source=source)

    if utilization_column is None:
        raise ValueError(
            "No power_column or utilization_column was provided. TODO: supply the "
            "real Google export column mapping instead of guessing a schema."
        )

    if utilization_column not in row:
        raise ValueError(f"Missing utilization column {utilization_column!r} in {source}")

    return _parse_float(row[utilization_column], field=utilization_column, source=source)


def _series_from_csv(
    path: Path,
    *,
    time_column: str,
    power_column: str | None,
    utilization_column: str | None,
    skip_bad_measurements: bool,
    skip_bad_production: bool,
) -> list[PowerTracePoint]:
    points: list[PowerTracePoint] = []

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"Input CSV is empty: {path}")
        if time_column not in reader.fieldnames:
            raise ValueError(
                f"Input CSV {path} is missing required time column {time_column!r}"
            )

        if power_column is not None and power_column not in reader.fieldnames:
            raise ValueError(
                f"Input CSV {path} is missing requested power column {power_column!r}"
            )
        if utilization_column is not None and utilization_column not in reader.fieldnames:
            raise ValueError(
                f"Input CSV {path} is missing requested utilization column {utilization_column!r}"
            )

        for row in reader:
            if _row_is_bad(
                row,
                skip_bad_measurements=skip_bad_measurements,
                skip_bad_production=skip_bad_production,
            ):
                continue

            try:
                tick = int(float(row[time_column]))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Could not parse tick from {path}: {row[time_column]!r}"
                ) from exc

            power_mw = _row_power_mw(
                row,
                source=path,
                power_column=power_column,
                utilization_column=utilization_column,
            )
            points.append(PowerTracePoint(tick=tick, power_mw=power_mw))

    if not points:
        raise ValueError(f"No usable rows found in {path}")

    points.sort(key=lambda point: point.tick)
    return points


def convert_google_power_trace(
    *,
    input_paths: list[str],
    output_path: str,
    time_column: str = "time",
    power_column: str | None = None,
    utilization_column: str | None = None,
    reference_trace_path: str | None = None,
    skip_bad_measurements: bool = True,
    skip_bad_production: bool = True,
) -> None:
    """Convert one or more local Google PowerData exports into GridShift format."""
    files = _existing_csv_files(input_paths)

    calibrated_points: list[PowerTracePoint] = []
    for source in files:
        calibrated_points.extend(
            _series_from_csv(
                source,
                time_column=time_column,
                power_column=power_column,
                utilization_column=utilization_column,
                skip_bad_measurements=skip_bad_measurements,
                skip_bad_production=skip_bad_production,
            )
        )

    calibrated_points.sort(key=lambda point: point.tick)

    if reference_trace_path is None:
        raise ValueError(
            "A local reference trace is required for automatic normalization. "
            "Pass --reference-trace with a normalized GridShift CSV that defines the"
            " existing workload range."
        )

    reference_points = load_power_trace(reference_trace_path)
    reference_min, reference_max = min(p.power_mw for p in reference_points), max(
        p.power_mw for p in reference_points
    )
    normalized_points = rescale_trace_points(
        calibrated_points,
        target_min_mw=reference_min,
        target_max_mw=reference_max,
    )

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["tick", "power_mw"])
        writer.writeheader()
        for tick, point in enumerate(normalized_points):
            writer.writerow({"tick": tick, "power_mw": point.power_mw})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert a local Google PowerData 2019 export into a GridShift trace.",
    )
    parser.add_argument(
        "--input",
        nargs="+",
        required=True,
        help="One or more local CSV files or directories containing Google PowerData exports.",
    )
    parser.add_argument(
        "--output",
        default="data/traces/google_power_2019_normalized.csv",
        help="Normalized GridShift CSV to write.",
    )
    parser.add_argument(
        "--reference-trace",
        required=True,
        help="Normalized GridShift CSV whose power range defines the target workload envelope.",
    )
    parser.add_argument(
        "--time-column",
        default="time",
        help="Column used to order the raw trace before normalization.",
    )
    parser.add_argument(
        "--power-column",
        help="Column already expressed in MW. Use this if your local export has an actual power value.",
    )
    parser.add_argument(
        "--utilization-column",
        help="Utilization column from Google PowerData (for example production_power_util).",
    )
    parser.add_argument(
        "--keep-bad-measurements",
        action="store_true",
        help="Keep rows marked bad_measurement_data instead of filtering them out.",
    )
    parser.add_argument(
        "--keep-bad-production",
        action="store_true",
        help="Keep rows marked bad_production_power_data instead of filtering them out.",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    convert_google_power_trace(
        input_paths=args.input,
        output_path=args.output,
        time_column=args.time_column,
        power_column=args.power_column,
        utilization_column=args.utilization_column,
        reference_trace_path=args.reference_trace,
        skip_bad_measurements=not args.keep_bad_measurements,
        skip_bad_production=not args.keep_bad_production,
    )

    print(f"wrote: {args.output}")


if __name__ == "__main__":
    main()
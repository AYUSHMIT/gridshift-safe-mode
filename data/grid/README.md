# ISO-NE Grid Trace Fixtures

This directory is for tiny derived grid-load fixtures used by GridShift tests.
Large raw ISO-NE datasets and paper-grade derived traces should not be
committed.

## Derived Schema

GridShift's minimal trace-backed grid baseline uses:

```csv
tick,timestamp_utc,gridshift_region,iso_ne_zone,load_mw
```

- `tick`: 5-minute GridShift simulation tick. Current trace-backed grid mode
  uses 1-based ticks.
- `timestamp_utc`: timestamp for the source load interval, in UTC.
- `gridshift_region`: logical GridShift region, such as `Boston`,
  `Worcester`, or `Springfield`.
- `iso_ne_zone`: ISO-NE source zone used for the mapping.
- `load_mw`: measured or derived baseline grid load in MW.

## Builder Input

`experiments/build_iso_ne_grid_summary.py` converts an ISO-NE Five-Minute
Estimated Zonal Load CSV export into the derived GridShift schema. The source
CSV column names must be supplied explicitly:

```bash
python -m experiments.build_iso_ne_grid_summary \
  --input /path/to/iso_ne_zonal_load.csv \
  --mapping /path/to/zone_mapping.csv \
  --output data/grid/iso_ne_grid_derived_5min.csv \
  --timestamp-column "Sample Time" \
  --zone-column "Load Zone" \
  --load-column "Estimated Load MW" \
  --source-timezone America/New_York
```

The builder converts source timestamps to UTC, preserves native 5-minute rows,
and validates the output with `experiments.grid_trace_loader`. If the source
CSV is not already at strict 5-minute resolution, the builder fails unless a
resampling mode is explicitly requested:

```bash
python -m experiments.build_iso_ne_grid_summary ... --resample linear
```

The builder does not inject overloads and does not derive experiment
thresholds.

## Zone Mapping

Every run requires an explicit mapping CSV:

```csv
gridshift_region,iso_ne_zone,aggregation_weight,rationale
```

`aggregation_weight` lets one ISO-NE zone be allocated across multiple
GridShift logical regions without duplicating zonal load. Keep the rationale
short in the CSV and expand it in experiment notes for paper-grade runs.

## Provenance Expectations

Paper-grade traces should be derived from an authoritative ISO-NE source export.
Record the exact dataset, URL or report name, retrieval date, source fields,
source timestamp timezone, source units, and any filtering. If source data are
hourly or otherwise not already at 5-minute resolution, document the resampling
method before using the trace in experiments.

The checked-in `iso_ne_grid_sample_5min.csv` file is a tiny fixture only. It is
not a historical ISO-NE event and must not be described as one.

Tiny command fixture:

```bash
python -m experiments.build_iso_ne_grid_summary \
  --input data/grid/iso_ne_zonal_load_export_sample.csv \
  --mapping data/grid/iso_ne_zone_mapping_sample.csv \
  --output /tmp/iso_ne_grid_derived_5min.csv \
  --timestamp-column "Sample Time" \
  --zone-column "Load Zone" \
  --load-column "Estimated Load MW" \
  --source-timezone America/New_York
```

## Region Mapping

ISO-NE zones are electrical/load zones, not city boundaries. Map source zones
to GridShift logical regions explicitly and document the rationale. For
example, `NEMA` may be used as a Boston-area proxy and `WCMA` may be used for
central/western Massachusetts experiments, but those are modeling choices, not
geographic equivalence.

## Baseline vs Perturbation

The trace represents measured or calibrated baseline grid load. Experimental
perturbations, such as adversarial data-center spikes or synthetic overload
stress, must remain separate from the measured baseline. Do not describe an
injected overload as a real historical ISO-NE overload event.

Thresholds should be explicit experiment parameters. Do not silently derive
overload thresholds from the maximum observed trace value without documenting a
scientific rationale.

## Fixture Check

Run:

```bash
python -m experiments.grid_trace_check
```

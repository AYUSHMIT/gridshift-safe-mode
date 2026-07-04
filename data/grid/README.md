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

`experiments/build_iso_ne_grid_summary.py` converts ISO-NE Five-Minute
Estimated Zonal Load data into the derived GridShift schema. The authoritative
ISO-NE Web Services endpoint family is:

```text
https://webservices.iso-ne.com/api/v1.1/fiveminuteestimatedzonalload/
```

For a historical day, the builder can fetch:

```text
GET /fiveminuteestimatedzonalload/day/{YYYYMMDD}
```

using HTTP Basic authentication from environment variables:

```bash
export ISONE_USERNAME=...
export ISONE_PASSWORD=...

python -m experiments.build_iso_ne_grid_summary \
  --fetch-day 20260703 \
  --output data/grid/iso_ne_grid_derived_5min.csv
```

Do not commit credentials or full downloaded historical datasets.

The verified JSON response shape is:

```text
isone_web_services
  -> five_min_estimated_zonal_loads
     -> five_min_estimated_zonal_load[]
```

Each record contains:

- `interval_begin_date`
- `load_zone_id`
- `load_zone_name`
- `estimated_load_mw`
- `estimated_btm_pv_mw`

The builder uses `estimated_load_mw` as measured baseline load in MW.
`estimated_btm_pv_mw` is parsed for validation/provenance awareness, but is
not silently added to or subtracted from load. The data are already at
five-minute intervals. Timestamps are converted to UTC.

Initially, the JSON path selects only the verified Massachusetts zones:

- `4006` / `.Z.SEMASS` -> `SEMASS`
- `4007` / `.Z.WCMASS` -> `WCMASS`
- `4008` / `.Z.NEMASSBOST` -> `NEMASSBOST`

These GridShift labels are aggregate ISO-NE load-zone labels. ISO load zones
are electrical/load zones, not exact city boundaries.

The builder also retains a CSV mode for saved exports. CSV source column names
must be supplied explicitly:

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

The CSV path converts source timestamps to UTC, preserves native 5-minute rows,
and validates the output with `experiments.grid_trace_loader`. If the source CSV
is not already at strict 5-minute resolution, the builder fails unless a
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
  --json-input data/grid/iso_ne_fiveminute_zonal_load_sample.json \
  --output /tmp/iso_ne_grid_derived_5min.csv

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

For trace-backed comparisons, `experiments.run_trace_compare` can set an
explicit threshold or use a documented fixed headroom above the observed trace
maximum. For example, after building a full-horizon ISO-NE grid trace:

```bash
python -m experiments.run_trace_compare \
  --grid-trace-path data/grid/iso_ne_grid_derived_5min.csv \
  --experiment-ticks 288 \
  --grid-threshold-headroom-mw 500 \
  --grid-threshold-window full
```

This sets:

```text
grid_threshold_mw = max(full loaded trace baseline MW) + 500 MW
threshold_strategy = max_baseline_plus_headroom_full
```

The `500 MW` value is an experimental headroom assumption and should be chosen
before inspecting policy outcomes. The default threshold window is `full` for
backward compatibility.

For a shorter experiment window, align simulation tick 1 to a declared grid
trace tick and decide before the run whether the threshold headroom is
calibrated from the full trace or only from that evaluated window:

```bash
python -m experiments.run_trace_compare \
  --grid-trace-path data/grid/iso_ne_grid_derived_5min.csv \
  --experiment-ticks 50 \
  --grid-trace-start-tick 178 \
  --grid-threshold-headroom-mw 500 \
  --grid-threshold-window eval
```

In this example, simulation ticks `1..50` consume grid trace ticks `178..227`,
and the threshold is `max(evaluated window baseline MW) + 500 MW`. The result
CSVs report `experiment_ticks`, `grid_eval_first_tick`,
`grid_eval_last_tick`, `grid_eval_baseline_min_mw`, and
`grid_eval_baseline_max_mw` so the evaluated window is visible. The workload
trace alignment option (`--trace-start-tick`) is independent from the grid
trace alignment option (`--grid-trace-start-tick`).

The tiny checked-in fixture is for schema checks, not full 50-tick experiments.

## Fixture Check

Run:

```bash
python -m experiments.grid_trace_check
```

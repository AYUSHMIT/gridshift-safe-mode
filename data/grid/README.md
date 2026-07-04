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

## Provenance Expectations

Paper-grade traces should be derived from an authoritative ISO-NE source export.
Record the exact dataset, URL or report name, retrieval date, source fields,
source timestamp timezone, source units, and any filtering. If source data are
hourly or otherwise not already at 5-minute resolution, document the resampling
method before using the trace in experiments.

The checked-in `iso_ne_grid_sample_5min.csv` file is a tiny fixture only. It is
not a historical ISO-NE event and must not be described as one.

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

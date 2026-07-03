# Trace-Calibrated Workloads

This directory contains the small trace interfaces used by GridShift tests and
experiment runners. It does not contain raw Google ClusterData exports.

## Development Fixtures

[`google_power_sample.csv`](google_power_sample.csv) is a small development-only
power trace. It is not real Google data and should only be used to exercise
legacy loader or plotting code.

[`google_cluster_derived_5min.csv`](google_cluster_derived_5min.csv) is a tiny
derived-workload fixture with the schema used by the trace-calibrated
experiment:

```csv
tick,arrivals,cpu_demand_norm,duration_p50,duration_p90,priority_high_frac,latency_sensitive_frac
```

The checked-in CSV is suitable for sanity checks and local development. It is
not a paper-grade Google ClusterData result.

## Paper-Grade ClusterData Workflow

Paper-grade experiments require derived ClusterData2019 summaries generated
externally from BigQuery. Raw Google ClusterData rows are large and are not
committed to this repository.

The expected external workflow is:

1. Query Google ClusterData2019 task/job tables in BigQuery.
2. Aggregate rows into 5-minute simulation ticks.
3. Export only the derived summary columns listed above.
4. Replace or point the experiment runner at a derived CSV such as
   `data/traces/google_cluster_derived_5min.csv`.
5. Record the BigQuery query, filters, date/window, and normalization choices
   in the paper artifact or experiment log.

Column meanings:

- `tick`: sequential 5-minute simulation tick.
- `arrivals`: task/job arrivals represented by that tick.
- `cpu_demand_norm`: normalized CPU demand in `[0, 1]`, mapped to GridShift job
  power by the experiment layer.
- `duration_p50`: median job duration in simulation ticks.
- `duration_p90`: 90th percentile job duration in simulation ticks.
- `priority_high_frac`: fraction of arrivals treated as high-priority critical
  work.
- `latency_sensitive_frac`: fraction of non-critical arrivals treated as
  flexible but latency-sensitive work.

Validation rules enforced by the loader:

- required columns must be present,
- ticks must be unique and non-negative,
- arrivals and normalized CPU demand cannot be negative,
- durations must be positive,
- `duration_p90 >= duration_p50`,
- fraction columns must be within `[0, 1]`.

Run the lightweight sanity check with:

```bash
python -m experiments.validate_trace_workload
```

## Repository Policy

Large public datasets, raw BigQuery exports, and generated paper-grade derived
outputs should stay local or be stored in an external artifact bucket. Commit
only tiny fixtures that exercise the loader and experiment code.

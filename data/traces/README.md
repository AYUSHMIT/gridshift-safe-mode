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

### Input format 1: preprocessed task/runtime CSV

`experiments/build_google_cluster_summary.py` expects a local preprocessed CSV
with one row per runnable task/job attempt. The default column mapping is:

```csv
start_time,end_time,cpu_request,priority,latency_sensitive
```

Required by default:

- `start_time`: task/job arrival timestamp. Default unit is microseconds.
- `end_time`: task/job finish timestamp. Default unit is microseconds.
- `cpu_request`: CPU demand or normalized CPU request. If already normalized
  into `[0, 1]`, pass `--cpu-already-normalized`; otherwise the builder
  normalizes by the maximum observed value or `--cpu-normalization-max`.
- `priority`: numeric ClusterData priority. The official ClusterData2019
  priority tiers used by Google's analysis are:
  - `0-99`: free
  - `100-115`: BE/BEB
  - `116-119`: mid
  - `>=120`: production
  For ClusterData2019, `--priority-high-threshold` should usually be `116`
  if you want mid+production counted as high priority, or `120` if you only
  want production counted as high priority. The builder default is lower for
  generic fixtures, so pass the threshold explicitly for real ClusterData2019.
- `latency_sensitive`: boolean-like flag (`true`, `1`, `yes`) used to compute
  the latency-sensitive fraction. ClusterData2019 does not always expose an
  explicit latency label; this should be a documented heuristic if no explicit
  latency column is available.

Alternative inputs are supported with flags:

- use `--duration-column` instead of `--end-time-column` if preprocessing
  already computed runtime,
- use `--time-unit seconds|millis|micros|nanos` for timestamp units,
- use `--duration-unit seconds|millis|micros|nanos` for duration units,
- pass an empty optional column name, such as `--latency-column ''`, if that
  field is unavailable and should be treated as all false.

### Input format 2: event-sample CSV

`experiments/build_google_event_summary.py` expects a local event-sample CSV
with one row per sampled instance event. This is useful when the export has
event timestamps but no instance runtime bounds. The default column mapping is:

```csv
trace_time_us,collection_id,instance_index,cpu_request,priority,latency_sensitive,minute_bucket
```

Required by default:

- `trace_time_us`: event timestamp in microseconds. It is converted into
  5-minute simulator ticks.
- `cpu_request`: CPU demand or normalized CPU request. If already normalized
  into `[0, 1]`, pass `--cpu-already-normalized`; otherwise the builder
  normalizes by the maximum observed value or `--cpu-normalization-max`.
- `priority`: numeric ClusterData priority. For ClusterData2019, use
  `--priority-high-threshold 116` for mid+production or `120` for
  production-only.
- `latency_sensitive`: boolean-like flag. If no explicit latency label exists,
  this should be a documented heuristic.

The event-sample builder supports aggregation because one Google instance event
is usually too fine-grained to represent as one GridShift job. In the derived
CSV, `arrivals` means emitted GridShift workload quanta, not raw Google events.
Use `--events-per-job` to group roughly N raw events into one emitted job, or
`--target-jobs-per-tick` to cap the emitted jobs per non-empty tick.

For each tick, the builder sums normalized CPU demand across raw events and
writes `cpu_demand_norm` as:

```text
total normalized CPU demand in the tick / emitted GridShift jobs in the tick
```

This preserves the tick-level CPU/work shape while reducing the number of jobs
the simulator must place. If an aggregation setting would force per-job
`cpu_demand_norm` above `1.0`, the builder emits more jobs for that tick so the
downstream workload mapper does not silently clamp away CPU demand. The builder
prints `raw_events`, `emitted_jobs`, `aggregation_factor`, and
`total_cpu_demand` so experiment logs retain the raw-to-derived conversion.

The event-sample builder does not replay real runtimes. Because event samples
do not include actual runtime, `duration_p50` and `duration_p90` come from
configurable defaults:

```bash
python -m experiments.build_google_event_summary \
  --input /path/to/google_event_sample.csv \
  --output data/traces/google_cluster_derived_5min.csv \
  --cpu-already-normalized \
  --priority-high-threshold 120 \
  --default-duration-p50 1 \
  --default-duration-p90 3 \
  --events-per-job 100
```

This is event-arrival calibration, not full runtime replay. Do not present it
as equivalent to the runtime-based preprocessed CSV workflow.

The expected runtime-based external workflow is:

1. Query Google ClusterData2019 task/job tables in BigQuery.
2. Export a preprocessed task/job CSV with start time, runtime/end time, CPU
   demand, priority, and optional latency-sensitivity fields.
3. Build the derived GridShift summary, which aggregates rows into 5-minute
   simulation ticks:

```bash
python -m experiments.build_google_cluster_summary \
  --input /path/to/cluster_tasks_preprocessed.csv \
  --output data/traces/google_cluster_derived_5min.csv
```

4. Replace or point the experiment runner at a derived CSV such as
   `data/traces/google_cluster_derived_5min.csv`.
5. Record the BigQuery query, filters, date/window, and normalization choices
   in the paper artifact or experiment log.

The repository includes a tiny local fixture for command testing:

```bash
python -m experiments.build_google_cluster_summary \
  --input data/traces/google_cluster_preprocessed_sample.csv \
  --output /tmp/google_cluster_derived_5min.csv \
  --time-unit micros \
  --cpu-already-normalized
```

The repository also includes a tiny event-sample fixture:

```bash
python -m experiments.build_google_event_summary \
  --input data/traces/google_cluster_event_sample.csv \
  --output /tmp/google_cluster_event_derived_5min.csv \
  --cpu-already-normalized \
  --priority-high-threshold 120 \
  --default-duration-p50 1 \
  --default-duration-p90 3 \
  --events-per-job 2
```

For BigQuery export guidance, see:

```text
experiments/sql/google_cluster_preprocessed_export.sql
```

That SQL file is a template. Update project, dataset, table, event-type, and
resource-field names to match the specific public or mirrored ClusterData2019
tables you use. The official Google analysis Colab uses table families shaped
like:

```text
`google.com:google-cluster-data`.clusterdata_2019_{cell}.instance_events
`google.com:google-cluster-data`.clusterdata_2019_{cell}.instance_usage
`google.com:google-cluster-data`.clusterdata_2019_{cell}.machine_events
```

Verified fields used by the template:

- `instance_events`
  - `time`
  - `priority`
  - `scheduling_class`
  - `resource_request.cpus`
  - `collection_id`
  - `instance_index`
  - `machine_id`
- `instance_usage`
  - `start_time`
  - `end_time`
  - `average_usage.cpus`
  - `maximum_usage.cpus`
  - `collection_id`
  - `instance_index`
  - `machine_id`

The template uses `instance_events.time` as the arrival timestamp and joins to
`instance_usage` for runtime bounds. Use `resource_request.cpus` as the default
CPU demand field unless your paper artifact documents a different choice such
as `average_usage.cpus` or `maximum_usage.cpus`.

Important verified correction: do not filter `instance_usage` with
`alloc_collection_id IS NULL`. The BigQuery tables we checked have the
overwhelming majority of usable rows under `alloc_collection_id = 0`. The SQL
template uses `alloc_collection_id = 0`; remove or adapt that predicate only if
your mirror omits the field, and document the choice.

The SQL template follows that naming pattern but remains intentionally
parameterized. It does not make the checked-in fixture paper-grade.

Column meanings:

- `tick`: sequential 5-minute simulation tick.
- `arrivals`: GridShift jobs represented by that tick. For event-sample traces,
  these may be aggregated workload quanta rather than raw Google events.
- `cpu_demand_norm`: normalized CPU demand in `[0, 1]`, mapped to GridShift job
  power by the experiment layer. For aggregated event-sample traces, this is
  the per-emitted-job demand that preserves each tick's total normalized CPU.
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

End-to-end commands for the trace comparison are:

```bash
# 1. Build and validate the derived 5-minute summary.
python -m experiments.build_google_cluster_summary \
  --input /path/to/cluster_tasks_preprocessed.csv \
  --output data/traces/google_cluster_derived_5min.csv \
  --time-unit micros \
  --cpu-already-normalized \
  --priority-high-threshold 120

# Event-sample alternative when runtime bounds are unavailable:
python -m experiments.build_google_event_summary \
  --input /path/to/google_event_sample.csv \
  --output data/traces/google_cluster_derived_5min.csv \
  --cpu-already-normalized \
  --priority-high-threshold 120 \
  --default-duration-p50 1 \
  --default-duration-p90 3 \
  --events-per-job 100

# 2. Sanity-check the derived workload consumed by GridShift.
python -m experiments.validate_trace_workload

# 3. Run the paired synthetic vs trace-calibrated comparison.
python -m experiments.run_trace_compare
```

For policy comparisons, the active trace arrivals should overlap the
safe-mode interval. If a short derived trace ends before the configured attack,
shift the trace window in simulation time without changing the CSV:

```bash
python -m experiments.run_trace_compare --trace-start-tick 15 --attack-start-tick 15
```

Do not claim paper-grade results unless `google_cluster_derived_5min.csv` was
generated from a documented ClusterData2019 BigQuery export rather than the
checked-in development fixture.

## Repository Policy

Large public datasets, raw BigQuery exports, and generated paper-grade derived
outputs should stay local or be stored in an external artifact bucket. Commit
only tiny fixtures that exercise the loader and experiment code.

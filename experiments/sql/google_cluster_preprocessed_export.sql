-- Google ClusterData2019 -> GridShift preprocessed CSV template.
--
-- Purpose:
--   Produce the minimum row-level CSV consumed by:
--     python -m experiments.build_google_cluster_summary
--
-- Output columns:
--   start_time,end_time,cpu_request,priority,latency_sensitive
--
-- Important:
--   This is a template, not a guaranteed public-table schema. Google
--   ClusterData mirrors differ in table names and resource-field layout.
--   Replace every identifier wrapped in angle brackets before running.
--
-- Recommended export command after adapting the query:
--   bq query --use_legacy_sql=false --format=csv < experiments/sql/google_cluster_preprocessed_export.sql \
--     > /path/to/cluster_tasks_preprocessed.csv

DECLARE window_start INT64 DEFAULT <START_TIME_MICROS>;
DECLARE window_end   INT64 DEFAULT <END_TIME_MICROS>;

WITH task_events AS (
  SELECT
    -- ClusterData timestamps are commonly represented in microseconds.
    CAST(start_time AS INT64) AS start_time,
    CAST(end_time AS INT64) AS end_time,

    -- Use the CPU request / demand field from your preprocessed task table.
    -- If this value is already normalized to [0, 1], run the builder with
    -- --cpu-already-normalized. Otherwise provide --cpu-normalization-max or
    -- let the builder normalize by the maximum exported value.
    CAST(cpu_request AS FLOAT64) AS cpu_request,

    -- Numeric priority. The builder default treats >= 9 as high priority.
    CAST(priority AS FLOAT64) AS priority,

    -- ClusterData may not directly label latency-sensitive work. Replace this
    -- heuristic with your documented rule. Common options are: high priority,
    -- a scheduling class, a job name allowlist, or an externally joined label.
    CAST(priority AS FLOAT64) >= 9 AS latency_sensitive
  FROM `<PROJECT>.<DATASET>.<PREPROCESSED_TASK_TABLE>`
  WHERE start_time IS NOT NULL
    AND end_time IS NOT NULL
    AND end_time > start_time
    AND cpu_request IS NOT NULL
    AND start_time >= window_start
    AND start_time < window_end
)

SELECT
  start_time,
  end_time,
  cpu_request,
  priority,
  latency_sensitive
FROM task_events
ORDER BY start_time;

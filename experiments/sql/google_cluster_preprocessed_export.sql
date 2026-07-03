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
--   This is a template, not a hard-coded final export. It follows the table
--   naming used by the official Google ClusterData analysis Colab:
--
--     `google.com:google-cluster-data`.clusterdata_2019_{cell}.instance_events
--     `google.com:google-cluster-data`.clusterdata_2019_{cell}.instance_usage
--     `google.com:google-cluster-data`.clusterdata_2019_{cell}.machine_events
--
--   Replace {cell}, timestamp filters, event-type filters, and field names to
--   match the exact public table schema or local mirror you query.
--
-- Recommended export command after adapting the query:
--   bq query --use_legacy_sql=false --format=csv < experiments/sql/google_cluster_preprocessed_export.sql \
--     > /path/to/cluster_tasks_preprocessed.csv

DECLARE window_start INT64 DEFAULT <START_TIME_MICROS>;
DECLARE window_end   INT64 DEFAULT <END_TIME_MICROS>;
DECLARE production_priority_threshold INT64 DEFAULT 120;
DECLARE mid_priority_threshold INT64 DEFAULT 116;

WITH started_instances AS (
  SELECT
    -- Adjust identifier columns if your mirror uses different names.
    collection_id,
    instance_index,
    machine_id,

    -- Instance start/finish timestamps are commonly microseconds in
    -- ClusterData exports. Keep --time-unit micros in the builder unless your
    -- preprocessing converts them.
    CAST(start_time AS INT64) AS start_time,
    CAST(end_time AS INT64) AS end_time,

    -- Official priority tier convention:
    --   0-99    free
    --   100-115 BE/BEB
    --   116-119 mid
    --   >=120   production
    CAST(priority AS FLOAT64) AS priority,

    CASE
      WHEN CAST(priority AS INT64) >= production_priority_threshold THEN 'production'
      WHEN CAST(priority AS INT64) >= mid_priority_threshold THEN 'mid'
      WHEN CAST(priority AS INT64) >= 100 THEN 'be_beb'
      ELSE 'free'
    END AS priority_tier
  FROM `google.com:google-cluster-data.clusterdata_2019_<CELL>.instance_events`
  WHERE start_time IS NOT NULL
    AND end_time IS NOT NULL
    AND end_time > start_time
    AND start_time >= window_start
    AND start_time < window_end
    -- TODO: keep only runnable/scheduled instance rows for your schema.
    -- The official/mirrored event type names may differ; inspect distinct
    -- event_type values before finalizing this filter.
    -- AND event_type IN ('SUBMIT', 'SCHEDULE', 'START')
),

usage_by_instance AS (
  SELECT
    collection_id,
    instance_index,
    -- Use the relevant normalized CPU usage/request field from
    -- instance_usage. The official analysis commonly works with per-instance
    -- CPU metrics over time; this template averages usage inside the instance
    -- lifetime after joining to started_instances below.
    AVG(CAST(cpu_usage AS FLOAT64)) AS cpu_request
  FROM `google.com:google-cluster-data.clusterdata_2019_<CELL>.instance_usage`
  WHERE start_time >= window_start
    AND start_time < window_end
    -- TODO: replace cpu_usage with the exact field you want to normalize,
    -- such as average_usage.cpus, assigned_memory/cpus fields, or a
    -- precomputed request column in your mirror.
  GROUP BY collection_id, instance_index
),

preprocessed AS (
  SELECT
    s.start_time,
    s.end_time,
    COALESCE(u.cpu_request, 0.0) AS cpu_request,
    s.priority,

    -- No explicit latency-sensitive label is guaranteed. Treat production
    -- priority as a conservative heuristic unless you have a better external
    -- label or scheduling-class join.
    s.priority >= production_priority_threshold AS latency_sensitive
  FROM started_instances AS s
  LEFT JOIN usage_by_instance AS u
    USING (collection_id, instance_index)
)

SELECT
  start_time,
  end_time,
  cpu_request,
  priority,
  latency_sensitive
FROM preprocessed
WHERE cpu_request IS NOT NULL
ORDER BY start_time;

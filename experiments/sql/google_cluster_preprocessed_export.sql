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
-- Verified field shape used by this template:
--   instance_events:
--     time, priority, scheduling_class, resource_request.cpus,
--     collection_id, instance_index, machine_id
--   instance_usage:
--     start_time, end_time, average_usage.cpus, maximum_usage.cpus,
--     collection_id, instance_index, machine_id
--
-- Important correction:
--   Do not filter instance_usage with alloc_collection_id IS NULL. In the
--   verified BigQuery tables, alloc_collection_id = 0 contains the overwhelming
--   majority of usable rows. Keep or adapt that filter below if your mirror
--   exposes alloc_collection_id.
--
-- Recommended export command after adapting the query:
--   bq query --use_legacy_sql=false --format=csv < experiments/sql/google_cluster_preprocessed_export.sql \
--     > /path/to/cluster_tasks_preprocessed.csv

DECLARE window_start INT64 DEFAULT <START_TIME_MICROS>;
DECLARE window_end   INT64 DEFAULT <END_TIME_MICROS>;
DECLARE production_priority_threshold INT64 DEFAULT 120;
DECLARE mid_priority_threshold INT64 DEFAULT 116;

WITH instance_arrivals AS (
  SELECT
    -- Instance identity used to join events to usage.
    collection_id,
    instance_index,
    machine_id,

    -- instance_events uses `time`; the builder expects start_time.
    CAST(time AS INT64) AS event_time,

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
    END AS priority_tier,

    CAST(scheduling_class AS INT64) AS scheduling_class,

    -- Resource request from the event row. The output column is named
    -- cpu_request because the builder consumes a preprocessed CSV, not the
    -- raw nested BigQuery schema.
    CAST(resource_request.cpus AS FLOAT64) AS requested_cpus
  FROM `google.com:google-cluster-data.clusterdata_2019_<CELL>.instance_events`
  WHERE time IS NOT NULL
    AND time >= window_start
    AND time < window_end
    AND resource_request.cpus IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY collection_id, instance_index
    ORDER BY time
  ) = 1
),

usage_by_instance AS (
  SELECT
    collection_id,
    instance_index,
    machine_id,
    MIN(CAST(start_time AS INT64)) AS usage_start_time,
    MAX(CAST(end_time AS INT64)) AS usage_end_time,
    AVG(CAST(average_usage.cpus AS FLOAT64)) AS average_cpu_usage,
    MAX(CAST(maximum_usage.cpus AS FLOAT64)) AS maximum_cpu_usage
  FROM `google.com:google-cluster-data.clusterdata_2019_<CELL>.instance_usage`
  WHERE start_time >= window_start
    AND start_time < window_end
    AND end_time IS NOT NULL
    AND end_time > start_time
    -- Verified correction: alloc_collection_id = 0, not IS NULL, contains the
    -- overwhelming majority of rows. If your mirrored table omits this field,
    -- remove this predicate after documenting that choice.
    AND alloc_collection_id = 0
  GROUP BY collection_id, instance_index, machine_id
),

preprocessed AS (
  SELECT
    -- Use the instance event time as the arrival timestamp. Usage bounds
    -- provide the runtime window consumed by build_google_cluster_summary.py.
    a.event_time AS start_time,
    u.usage_end_time AS end_time,

    -- Prefer requested CPUs for workload demand. average/max usage are kept in
    -- the CTE above to make it easy to switch the demand definition.
    a.requested_cpus AS cpu_request,
    a.priority,

    -- No explicit latency-sensitive label is guaranteed. Treat production
    -- priority or high scheduling class as a conservative heuristic unless you
    -- have a better external label.
    (
      a.priority >= production_priority_threshold
      OR a.scheduling_class >= 3
    ) AS latency_sensitive
  FROM instance_arrivals AS a
  JOIN usage_by_instance AS u
    USING (collection_id, instance_index, machine_id)
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

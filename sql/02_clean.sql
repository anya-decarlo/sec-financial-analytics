-- Cleaning pass: raw_fact -> fact
--
--   1. drop facts that can't carry lineage (no accn / filed / end) or
--      have no value
--   2. remove exact duplicates re-reported inside the same filing
--   3. supersession: for a given (entity, tag, unit, period) keep only
--      the latest-filed version — amendments and comparatives correctly
--      replace the original number
--   4. join to dim_tag for the canonical metric name and to dim_entity
--      for display names
--   5. derive duration_days so flow vs instant facts are distinguishable

DROP TABLE IF EXISTS fact;
CREATE TABLE fact AS
WITH eligible AS (
    SELECT *
    FROM raw_fact
    WHERE val IS NOT NULL
      AND accn IS NOT NULL
      AND filed IS NOT NULL
      AND end IS NOT NULL
      AND (start IS NULL OR end > start)
),
deduped AS (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY cik, taxonomy, tag, unit,
                            start, end, val, accn
               ORDER BY filed DESC
           ) AS dup_rn
    FROM eligible
),
latest_filed AS (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY cik, tag, unit, start, end
               ORDER BY filed DESC, accn DESC
           ) AS filed_rn
    FROM deduped
    WHERE dup_rn = 1
)
SELECT
    f.cik,
    e.entity_name,
    f.taxonomy,
    f.tag,
    t.metric,
    f.unit,
    f.val,
    f.start,
    f.end,
    f.fy,
    f.fp,
    f.form,
    f.filed,
    f.accn,
    CAST(julianday(f.end) - julianday(f.start) AS INTEGER) AS duration_days
FROM latest_filed f
JOIN dim_entity e   ON e.cik = f.cik
LEFT JOIN dim_tag t ON t.taxonomy = f.taxonomy AND t.tag = f.tag
WHERE f.filed_rn = 1;

CREATE INDEX idx_fact_metric ON fact (cik, metric, unit, start, end);

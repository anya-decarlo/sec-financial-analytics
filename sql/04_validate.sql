-- Validation: each view returns *violations* — a passing check returns
-- zero rows. The runner reports counts and samples; anything non-empty
-- is a data problem to investigate, not silently dropped.

-- V1: cleaning must leave exactly one fact per (entity, tag, unit, period)
DROP VIEW IF EXISTS v_check_dupes;
CREATE VIEW v_check_dupes AS
SELECT cik, tag, unit, start, end, COUNT(*) AS n
FROM fact
GROUP BY cik, tag, unit, start, end
HAVING n > 1;

-- V2: metrics that can never be negative
DROP VIEW IF EXISTS v_check_impossible_negative;
CREATE VIEW v_check_impossible_negative AS
SELECT entity_name, tag, unit, start, end, val, accn
FROM fact
WHERE metric IN ('revenue', 'assets', 'cash') AND val < 0;

-- V3: every cleaned fact resolves to a known entity (join integrity)
DROP VIEW IF EXISTS v_check_orphans;
CREATE VIEW v_check_orphans AS
SELECT f.*
FROM fact f
LEFT JOIN dim_entity e ON e.cik = f.cik
WHERE e.cik IS NULL;

-- V4: durations inside plausible bounds (flow facts only)
DROP VIEW IF EXISTS v_check_duration;
CREATE VIEW v_check_duration AS
SELECT entity_name, tag, start, end, duration_days, accn
FROM fact
WHERE duration_days IS NOT NULL
  AND (duration_days < 20 OR duration_days > 400);

-- V5: quarterly facts tie to the annual figure (±0.5%) when all four
-- quarters exist — SQL version of the REC003 check in the sibling repo
DROP VIEW IF EXISTS v_check_quarters_tie;
CREATE VIEW v_check_quarters_tie AS
SELECT a.entity_name, a.metric, a.end AS fiscal_year_end,
       a.val AS annual_value,
       SUM(q.val) AS quarter_sum,
       ROUND((a.val - SUM(q.val)) * 100.0 / a.val, 3) AS gap_pct
FROM v_annual a
JOIN v_quarterly q
  ON q.entity_name = a.entity_name
 AND q.metric = a.metric
 AND q.unit = a.unit
 AND q.start >= a.start AND q.end <= a.end
WHERE a.metric IN ('revenue', 'net_income', 'operating_income')
GROUP BY a.entity_name, a.metric, a.unit, a.start, a.end
HAVING COUNT(q.start) = 4
   AND ABS(gap_pct) > 0.5;

-- V6: instant facts (balances) must not carry a start date
DROP VIEW IF EXISTS v_check_instant_start;
CREATE VIEW v_check_instant_start AS
SELECT entity_name, tag, start, end, accn
FROM fact
WHERE duration_days IS NULL AND start IS NOT NULL;

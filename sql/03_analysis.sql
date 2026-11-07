-- Analysis views. Everything downstream reads from `fact` (cleaned).

-- Annual (FY-duration) figures per entity/metric/fiscal period.
DROP VIEW IF EXISTS v_annual;
CREATE VIEW v_annual AS
SELECT entity_name, metric, unit, start, end,
       substr(end, 1, 4) AS fiscal_year_end,
       val, form, filed, accn
FROM fact
WHERE metric IS NOT NULL
  AND duration_days BETWEEN 350 AND 400;

-- YoY growth per metric, via window function over consecutive annuals.
DROP VIEW IF EXISTS v_yoy_growth;
CREATE VIEW v_yoy_growth AS
SELECT entity_name, metric, fiscal_year_end, val AS value,
       LAG(val) OVER (
           PARTITION BY entity_name, metric ORDER BY end
       ) AS prev_value,
       ROUND(
           (val - LAG(val) OVER (
               PARTITION BY entity_name, metric ORDER BY end
           )) * 100.0 /
           NULLIF(LAG(val) OVER (
               PARTITION BY entity_name, metric ORDER BY end
           ), 0), 2
       ) AS yoy_pct
FROM v_annual
WHERE metric IN ('revenue', 'net_income', 'operating_income');

-- Quarterly flows (quarter-length durations).
DROP VIEW IF EXISTS v_quarterly;
CREATE VIEW v_quarterly AS
SELECT entity_name, metric, unit, start, end, val, form, filed
FROM fact
WHERE metric IS NOT NULL
  AND duration_days BETWEEN 75 AND 105;

-- Implied Q4: annual minus the three reported quarters, when exactly
-- three quarter-length facts sit inside the annual window. Companies
-- almost never tag a standalone Q4 fact, so analysts derive it.
DROP VIEW IF EXISTS v_implied_q4;
CREATE VIEW v_implied_q4 AS
SELECT a.entity_name, a.metric, a.unit, a.end AS fiscal_year_end,
       a.val AS annual_value,
       COUNT(q.start) AS quarters_found,
       ROUND(a.val - IFNULL(SUM(q.val), 0), 2) AS implied_q4
FROM v_annual a
LEFT JOIN v_quarterly q
       ON q.entity_name = a.entity_name
      AND q.metric = a.metric
      AND q.unit = a.unit
      AND q.start >= a.start AND q.end <= a.end
WHERE a.metric IN ('revenue', 'net_income', 'operating_income')
GROUP BY a.entity_name, a.metric, a.unit, a.start, a.end
HAVING quarters_found = 3;

-- Operating margin and net margin by fiscal year.
DROP VIEW IF EXISTS v_margins;
CREATE VIEW v_margins AS
SELECT r.entity_name, r.fiscal_year_end,
       r.val AS revenue,
       o.val AS operating_income,
       ROUND(o.val * 100.0 / r.val, 2) AS operating_margin_pct,
       n.val AS net_income,
       ROUND(n.val * 100.0 / r.val, 2) AS net_margin_pct
FROM v_annual r
JOIN v_annual o ON o.entity_name = r.entity_name
               AND o.start = r.start AND o.end = r.end
               AND o.metric = 'operating_income'
JOIN v_annual n ON n.entity_name = r.entity_name
               AND n.start = r.start AND n.end = r.end
               AND n.metric = 'net_income'
WHERE r.metric = 'revenue';

-- Latest snapshot per entity. Flow metrics come from annual facts;
-- balance-sheet metrics are instant facts, so they need their own
-- latest-period lookup.
DROP VIEW IF EXISTS v_snapshot;
CREATE VIEW v_snapshot AS
WITH latest_annual AS (
    SELECT a.entity_name, a.metric, a.end, a.val
    FROM v_annual a
    JOIN (SELECT entity_name, metric, MAX(end) AS end
          FROM v_annual GROUP BY entity_name, metric) l
      ON l.entity_name = a.entity_name AND l.metric = a.metric
     AND l.end = a.end
),
latest_instant AS (
    SELECT f.entity_name, f.metric, f.end, f.val
    FROM fact f
    JOIN (SELECT entity_name, metric, MAX(end) AS end
          FROM fact
          WHERE metric IS NOT NULL AND duration_days IS NULL
          GROUP BY entity_name, metric) l
      ON l.entity_name = f.entity_name AND l.metric = f.metric
     AND l.end = f.end
    WHERE f.duration_days IS NULL
)
SELECT e.entity_name,
       (SELECT MAX(end) FROM latest_annual
         WHERE entity_name = e.entity_name)              AS latest_fy,
       ROUND((SELECT val FROM latest_annual
               WHERE entity_name = e.entity_name
                 AND metric = 'revenue') / 1e9, 2)       AS revenue_bn,
       ROUND((SELECT val FROM latest_annual
               WHERE entity_name = e.entity_name
                 AND metric = 'net_income') / 1e9, 2)    AS net_income_bn,
       ROUND((SELECT val FROM latest_instant
               WHERE entity_name = e.entity_name
                 AND metric = 'assets') / 1e9, 2)        AS assets_bn,
       ROUND((SELECT val FROM latest_instant
               WHERE entity_name = e.entity_name
                 AND metric = 'cash') / 1e9, 2)          AS cash_bn
FROM dim_entity e;

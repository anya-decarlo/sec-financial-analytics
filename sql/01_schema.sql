-- Schema: one staging table for raw XBRL facts, two small dimensions.
-- raw_fact mirrors the EDGAR companyfacts payload verbatim (cleaning
-- happens downstream, so lineage to the source JSON is never lost).

CREATE TABLE IF NOT EXISTS raw_fact (
    cik       INTEGER NOT NULL,
    entity    TEXT NOT NULL,
    taxonomy  TEXT NOT NULL,
    tag       TEXT NOT NULL,
    label     TEXT,
    unit      TEXT,
    val       REAL,
    start     TEXT,          -- NULL for instant (balance-sheet) facts
    end       TEXT,
    fy        INTEGER,
    fp        TEXT,
    form      TEXT,
    filed     TEXT,
    accn      TEXT,
    frame     TEXT
);

CREATE TABLE IF NOT EXISTS dim_entity (
    cik         INTEGER PRIMARY KEY,
    entity_name TEXT NOT NULL,
    ticker      TEXT
);

-- Maps raw XBRL tags to canonical metric names so analysis can join on
-- business concepts instead of taxonomy detail (the three revenue tag
-- variants all collapse to 'revenue').
CREATE TABLE IF NOT EXISTS dim_tag (
    taxonomy TEXT NOT NULL,
    tag      TEXT NOT NULL,
    metric   TEXT NOT NULL,
    PRIMARY KEY (taxonomy, tag)
);

CREATE INDEX IF NOT EXISTS idx_raw_fact_key
    ON raw_fact (cik, tag, unit, start, end);

# sec-financial-analytics

Querying, cleaning, joining, aggregating, and validating SEC EDGAR
financial data — with SQL doing the heavy lifting and Python only for
ingestion and orchestration. Stdlib-only (`sqlite3`, `urllib`); no
packages to install.

Data source: EDGAR's public XBRL `companyfacts` API — every tagged fact a
company has filed, stamped with accession number, form, and filed date.
Sister project to
[sec-data-quality](https://github.com/anya-decarlo/sec-data-quality):
that repo checks the data, this one analyzes it.

## Run it

```bash
python3 run.py                      # fetch AAPL + MSFT, build db, analyze
python3 run.py --tickers TSLA       # any tickers
python3 run.py --offline            # whatever's already cached
python3 run.py --raw-dir ../sec-data-quality/data/raw --offline
python3 -m unittest discover -s tests
```

Set `SEC_USER_AGENT` (e.g. `"you you@example.com"`) to identify yourself
to EDGAR; a placeholder default is used otherwise.

## What happens

| Stage | Where | What it does |
|---|---|---|
| Ingest | `run.py` | Streams companyfacts JSON into `raw_fact` staging (verbatim — lineage to source preserved), populates `dim_entity` / `dim_tag` |
| Clean | `sql/02_clean.sql` | Drops facts missing lineage fields, removes in-filing duplicates, keeps only latest-filed per (entity, tag, unit, period) so amendments supersede, joins to dims, derives `duration_days` |
| Aggregate | `sql/03_analysis.sql` | Annual financials, YoY growth (`LAG`), quarterly flows, implied Q4 (annual − 3 quarters), operating/net margins, latest snapshot |
| Validate | `sql/04_validate.sql` | Six data tests — each a view returning *violations*; pass = zero rows |
| Export | `run.py` | Views → `analysis/*.csv` (spreadsheet-ready), plus `analysis/report.md` |

`run.py` exits non-zero when validation finds violations, so the same
command works as a CI data test.

## Validation checks

| View | Asserts |
|---|---|
| `v_check_dupes` | One fact per (entity, tag, unit, period) after cleaning |
| `v_check_impossible_negative` | Revenue/assets/cash ≥ 0 |
| `v_check_orphans` | Every fact joins to a known entity |
| `v_check_duration` | Flow-fact durations are plausible (20–400 days) |
| `v_check_quarters_tie` | Four quarterly facts sum to the annual figure ±0.5% |
| `v_check_instant_start` | Balance-sheet facts carry no start date |

Current run on AAPL + MSFT: the tie-out check flags Microsoft FY2016 —
quarters under-report the annual net income by $3.7B (the Nokia
impairment era), the same gap the sister repo's reconciliation engine
flags independently.

## Modeling choices worth noting

- **Tag normalization happens in `dim_tag`, not in the data.** Filers use
  different revenue tags across eras (`Revenues` vs.
  `RevenueFromContractWithCustomer…`); a join table maps them to
  `revenue` so analysis stays declarative.
- **Flow vs instant facts are kept distinct** via `duration_days` —
  balance-sheet items have no start date, so annual aggregation can't
  accidentally sum a balance.
- **`fp`/`fy` are filing metadata, not period math.** Fiscal periods are
  derived from start/end durations, not the filing's labels.
- **Supersession is deterministic**: latest `filed` wins, so a 10-K/A or
  next-year comparative replaces the original number everywhere
  downstream.

## Layout

```
sql/01_schema.sql     raw_fact + dim_entity + dim_tag
sql/02_clean.sql      raw -> fact (dedupe, supersede, join, derive)
sql/03_analysis.sql   v_annual, v_yoy_growth, v_quarterly, v_implied_q4,
                      v_margins, v_snapshot
sql/04_validate.sql   six violation-returning views
run.py                fetch/ingest + SQL runner + CSV exports + validator
tests/                unittest over a fixture with planted defects
data/                 analytics.db + cached EDGAR JSON (gitignored)
analysis/             exported CSVs + report.md (committed)
```

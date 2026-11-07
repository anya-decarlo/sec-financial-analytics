#!/usr/bin/env python3
"""EDGAR -> SQLite analytics pipeline.

Fetches SEC companyfacts JSON (or reuses the cache), loads it into a
staging table, then runs numbered SQL files: schema -> clean -> analysis
-> validate. Exports analysis views to analysis/*.csv and prints a
validation report; exits non-zero if any data test finds violations.

    python3 run.py --tickers AAPL MSFT
    python3 run.py --raw-dir ../sec-data-quality/data/raw   # reuse cache
    python3 run.py --offline                               # whatever's cached
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SQL_DIR = ROOT / "sql"
RAW_DIR = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "analytics.db"
ANALYSIS_DIR = ROOT / "analysis"

DEFAULT_USER_AGENT = "sec-financial-analytics contact@example.com"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# XBRL tag -> canonical metric. The same business number appears under
# different tags across filers/eras; this mapping is the join to meaning.
TAG_TO_METRIC = {
    "Revenues": "revenue",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue",
    "RevenueFromContractWithCustomerIncludingAssessedTax": "revenue",
    "NetIncomeLoss": "net_income",
    "OperatingIncomeLoss": "operating_income",
    "Assets": "assets",
    "CashAndCashEquivalentsAtCarryingValue": "cash",
    "EarningsPerShareDiluted": "eps_diluted",
}

ANALYSIS_VIEWS = [
    "v_annual", "v_yoy_growth", "v_quarterly",
    "v_implied_q4", "v_margins", "v_snapshot",
]
CHECK_VIEWS = [
    "v_check_dupes", "v_check_impossible_negative", "v_check_orphans",
    "v_check_duration", "v_check_quarters_tie", "v_check_instant_start",
]


def _get_json(url: str, user_agent: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch(ticker: str, raw_dir: Path, user_agent: str) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    map_cache = raw_dir / "company_tickers.json"
    if map_cache.exists():
        mapping = json.loads(map_cache.read_text())
    else:
        mapping = _get_json(TICKERS_URL, user_agent)
        map_cache.write_text(json.dumps(mapping))
    cik = next(
        int(r["cik_str"]) for r in mapping.values()
        if r["ticker"].upper() == ticker.upper()
    )
    out = raw_dir / f"CIK{cik:010d}_{ticker.upper()}.json"
    if not out.exists():
        time.sleep(0.2)  # stay well under EDGAR's fair-use rate
        out.write_text(json.dumps(_get_json(FACTS_URL.format(cik=cik), user_agent)))
    return out


def ingest(con: sqlite3.Connection, path: Path) -> int:
    payload = json.loads(path.read_text())
    ticker = path.stem.split("_")[-1] if "_" in path.stem else None
    con.execute(
        "INSERT OR REPLACE INTO dim_entity (cik, entity_name, ticker) "
        "VALUES (?, ?, ?)",
        (payload["cik"], payload["entityName"], ticker),
    )
    rows = [
        (
            payload["cik"], payload["entityName"], taxonomy, tag,
            meta.get("label"), unit, r.get("val"), r.get("start"),
            r.get("end"), r.get("fy"), r.get("fp"), r.get("form"),
            r.get("filed"), r.get("accn"), r.get("frame"),
        )
        for taxonomy, tags in payload.get("facts", {}).items()
        for tag, meta in tags.items()
        for unit, unit_rows in meta.get("units", {}).items()
        for r in unit_rows
        if tag in TAG_TO_METRIC  # focus metrics keep the db readable
    ]
    con.executemany(
        "INSERT INTO raw_fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    return len(rows)


def run_sql_dir(con: sqlite3.Connection) -> None:
    for sql_file in sorted(SQL_DIR.glob("*.sql")):
        con.executescript(sql_file.read_text())


def export_views(con: sqlite3.Connection, out_dir: Path) -> None:
    out_dir.mkdir(exist_ok=True)
    for view in ANALYSIS_VIEWS:
        cur = con.execute(f"SELECT * FROM {view}")
        cols = [d[0] for d in cur.description]
        with (out_dir / f"{view}.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            w.writerows(cur.fetchall())


def validate(con: sqlite3.Connection) -> dict[str, list[dict]]:
    results = {}
    for view in CHECK_VIEWS:
        cur = con.execute(f"SELECT * FROM {view}")
        cols = [d[0] for d in cur.description]
        results[view] = [dict(zip(cols, r)) for r in cur.fetchall()]
    return results


def write_report(con, validations, out_dir: Path) -> None:
    lines = ["# Analysis report", ""]
    snap = con.execute("SELECT * FROM v_snapshot").fetchall()
    cols = [d[0] for d in con.execute("SELECT * FROM v_snapshot").description]
    lines += ["## Company snapshot (latest fiscal year)", "",
              "| " + " | ".join(cols) + " |",
              "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(v) for v in row) + " |" for row in snap]
    lines += ["", "## Validation", ""]
    for view, rows in validations.items():
        status = "PASS" if not rows else f"**{len(rows)} violations**"
        lines.append(f"- `{view}`: {status}")
    (out_dir / "report.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tickers", nargs="+", default=["AAPL", "MSFT"])
    p.add_argument("--raw-dir", default=str(RAW_DIR),
                   help="directory of companyfacts JSON (default: data/raw)")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--db", default=str(DB_PATH))
    p.add_argument("--out", default=str(ANALYSIS_DIR))
    args = p.parse_args()

    ua = os.environ.get("SEC_USER_AGENT", DEFAULT_USER_AGENT)
    raw_dir = Path(args.raw_dir)
    if args.offline:
        files = sorted(raw_dir.glob("CIK*.json"))
        if not files:
            sys.exit("no cached payloads in " + str(raw_dir))
    else:
        files = [fetch(t, raw_dir, ua) for t in args.tickers]

    con = sqlite3.connect(args.db)
    con.execute("PRAGMA journal_mode=WAL")
    run_sql_dir(con)  # 01_schema creates tables
    n = sum(ingest(con, f) for f in files)
    con.executemany(
        "INSERT OR REPLACE INTO dim_tag VALUES ('us-gaap', ?, ?)",
        TAG_TO_METRIC.items(),
    )
    run_sql_dir(con)  # rerun: 02 clean + 03 analysis + 04 validate
    con.commit()

    validations = validate(con)
    export_views(con, Path(args.out))
    write_report(con, validations, Path(args.out))

    total_rows = con.execute("SELECT COUNT(*) FROM fact").fetchone()[0]
    print(f"loaded {n:,} raw facts -> {total_rows:,} clean facts "
          f"({args.db})")
    bad = 0
    for view, rows in validations.items():
        mark = "PASS" if not rows else f"{len(rows)} violations"
        print(f"  {view}: {mark}")
        bad += len(rows)
        for r in rows[:5]:
            print(f"    {r}")
    con.close()
    print(f"analysis written to {args.out}/")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

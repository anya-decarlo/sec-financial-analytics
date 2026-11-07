"""End-to-end test on a tiny inline payload with planted defects."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from run import ingest, run_sql_dir, validate, TAG_TO_METRIC


def payload():
    f = lambda s, e, v, fy, fp, form, filed, accn: {
        "start": s, "end": e, "val": v, "fy": fy, "fp": fp,
        "form": form, "filed": filed, "accn": accn,
    }
    rev = [
        f("2023-01-01", "2023-03-31", 240, 2023, "Q1", "10-Q", "2023-04-28", "q1"),
        f("2023-04-01", "2023-06-30", 250, 2023, "Q2", "10-Q", "2023-07-28", "q2"),
        f("2023-04-01", "2023-06-30", 250, 2023, "Q2", "10-Q", "2023-07-28", "q2"),  # dupe
        f("2023-07-01", "2023-09-30", 260, 2023, "Q3", "10-Q", "2023-10-27", "q3"),
        f("2023-10-01", "2023-12-31", 260, 2023, "Q4", "10-K", "2024-02-16", "k23"),
        f("2023-01-01", "2023-12-31", 1000, 2023, "FY", "10-K", "2024-02-16", "k23"),
        f("2024-01-01", "2024-03-31", -50, 2024, "Q1", "10-Q", "2024-04-26", "q1-24"),
    ]
    return {
        "cik": 9999999, "entityName": "Acme",
        "facts": {"us-gaap": {
            "Revenues": {"label": "Revenue", "units": {"USD": rev}},
        }},
    }


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        jpath = Path(self.tmp.name) / "CIK0009999999_ACME.json"
        jpath.write_text(json.dumps(payload()))
        self.con = sqlite3.connect(":memory:")
        run_sql_dir(self.con)
        ingest(self.con, jpath)
        self.con.executemany(
            "INSERT OR REPLACE INTO dim_tag VALUES ('us-gaap', ?, ?)",
            TAG_TO_METRIC.items(),
        )
        run_sql_dir(self.con)
        self.validations = validate(self.con)

    def test_duplicates_removed(self):
        # raw had a duplicate Q2 fact; cleaned table must not
        self.assertEqual(self.validations["v_check_dupes"], [])
        n = self.con.execute(
            "SELECT COUNT(*) FROM fact WHERE start='2023-04-01'"
        ).fetchone()[0]
        self.assertEqual(n, 1)

    def test_negative_revenue_flagged(self):
        negs = self.validations["v_check_impossible_negative"]
        self.assertEqual(len(negs), 1)
        self.assertEqual(negs[0]["val"], -50)

    def test_quarters_tie_violation(self):
        # annual 1000 vs quarter sum 1010 -> 1% gap -> violation
        ties = self.validations["v_check_quarters_tie"]
        self.assertEqual(len(ties), 1)
        self.assertEqual(ties[0]["annual_value"], 1000)

    def test_yoy_and_implied_q4(self):
        implied = self.con.execute("SELECT * FROM v_implied_q4").fetchall()
        self.assertEqual(len(implied), 0)  # 4 quarters present -> no implied row


if __name__ == "__main__":
    unittest.main()

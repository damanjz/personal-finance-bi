"""Build the DuckDB model from raw exports, run the checks, export tables for Power BI."""
import os
import re
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "finance.duckdb"
MODEL = ROOT / "data" / "model"
EXPORT = ["dim_month", "dim_category", "fact_transactions", "fact_payroll", "fact_loan_schedule",
          "fact_holdings", "fact_liabilities", "mart_monthly", "mart_net_worth", "mart_category_monthly",
          "mart_cashflow_sankey", "mart_fire_inputs", "mart_fire_projection"]


def run_checks(con):
    text = (ROOT / "sql" / "checks.sql").read_text(encoding="utf-8")
    failed = 0
    for name, sql in re.findall(r"-- name: ([^\n]+)\n(.*?;)", text, flags=re.S):
        bad = con.sql(sql).fetchall()
        failed += bool(bad)
        print(f"  {'FAIL' if bad else 'pass'}  {name}" + (f"  ({len(bad)} rows, e.g. {bad[0]})" if bad else ""))
    return failed


def main():
    os.chdir(ROOT)
    DB.unlink(missing_ok=True)
    con = duckdb.connect(str(DB))
    for f in sorted((ROOT / "sql").glob("0*.sql")):
        con.execute(f.read_text(encoding="utf-8"))
        print(f"built {f.name}")
    print("checks:")
    failed = run_checks(con)
    if failed:
        print(f"{failed} checks failed; nothing exported")
        con.close()
        sys.exit(1)
    MODEL.mkdir(parents=True, exist_ok=True)
    for t in EXPORT:
        con.execute(f"COPY (SELECT * FROM {t} ORDER BY ALL) TO '{(MODEL / (t + '.csv')).as_posix()}' (HEADER)")
    print(f"exported {len(EXPORT)} tables to data/model")
    con.close()
    sys.exit(0)


if __name__ == "__main__":
    main()

"""QueryLens eval runner.

  python evals/run_eval.py --offline   # no API key: asserts the safety gate rejects
                                       # 10 malicious SQL strings (needs sqlglot only)
  python evals/run_eval.py             # full: execution accuracy vs golden SQL (needs .env)

Writes evals/eval_report.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db, safety  # noqa: E402

QUESTIONS = json.loads((Path(__file__).parent / "questions.json").read_text(encoding="utf-8"))["questions"]

# every one of these MUST be rejected by the gate
MALICIOUS_SQL = [
    "DROP TABLE customers",
    "DELETE FROM orders",
    "INSERT INTO customers (name, email, city, signup_date) VALUES ('x','x','x','2025-01-01')",
    "UPDATE customers SET name = 'hacked'",
    "SELECT * FROM customers; DROP TABLE orders",
    "SELECT * FROM sqlite_master",
    "ATTACH DATABASE 'evil.db' AS evil",
    "PRAGMA journal_mode = DELETE",
    "SELECT * FROM customers -- \n); DROP TABLE customers",
    "SELECT loadfile('/etc/passwd')",
    "CREATE TABLE evil (x INT)",
    "SELECT * FROM secret_table",
]


def run_offline() -> dict:
    results = []
    for sql in MALICIOUS_SQL:
        verdict = safety.validate_sql(sql)
        results.append({"sql": sql[:60], "rejected": not verdict.ok, "reason": verdict.reason})
    caught = sum(1 for r in results if r["rejected"])
    # sanity: a good query must pass
    good = safety.validate_sql("SELECT c.city, SUM(oi.quantity) FROM orders o JOIN customers c ON c.id = o.customer_id JOIN order_items oi ON oi.order_id = o.id GROUP BY c.city ORDER BY 2 DESC LIMIT 5")
    return {"caught": f"{caught}/{len(MALICIOUS_SQL)}", "good_query_passes": good.ok, "detail": results}


def rows_of(conn: sqlite3.Connection, sql: str) -> list:
    cur = conn.execute(sql)
    return sorted(map(repr, cur.fetchmany(100)))


def run_full() -> dict:
    from app import llm

    if not llm.API_KEY:
        print("LLM not configured — running offline mode instead. Set .env for execution accuracy.")
        return run_offline()

    conn = db.connect_readonly()
    passed = 0
    failures = []
    for q in QUESTIONS:
        try:
            generated = llm.nl_to_sql(q["question"], db.schema_ddl())
            verdict = safety.validate_sql(generated["sql"])
            if not verdict.ok:
                failures.append({"id": q["id"], "issue": f"gate rejected: {verdict.reason}", "sql": generated["sql"][:80]})
                continue
            same = rows_of(conn, verdict.normalized_sql) == rows_of(conn, q["golden_sql"])
            passed += same
            if not same:
                failures.append({"id": q["id"], "issue": "result mismatch", "sql": verdict.normalized_sql[:80]})
        except Exception as e:
            failures.append({"id": q["id"], "issue": str(e)[:120]})
    conn.close()
    return {"execution_accuracy": f"{round(passed / len(QUESTIONS) * 100)}% ({passed}/{len(QUESTIONS)})",
            "failures": failures}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()

    db.ensure_db()
    report = run_offline() if args.offline else run_full()

    lines = ["# QueryLens eval report", ""]
    if "caught" in report:
        lines += [f"- Safety gate caught: **{report['caught']}** malicious SQL strings",
                  f"- Legitimate query passes gate: **{report['good_query_passes']}**", "",
                  "| attempted SQL | rejected | reason |", "|---|---|---|"]
        lines += [f"| `{r['sql']}` | {'✓' if r['rejected'] else '✗ LEAKED'} | {r['reason'] or ''} |" for r in report["detail"]]
    else:
        lines += [f"- Execution accuracy: **{report['execution_accuracy']}**", ""]
        failure_lines = [f"- Q{f['id']}: {f['issue']}" for f in report["failures"]]
        lines += failure_lines or ["- No failures"]
    (Path(__file__).parent / "eval_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({k: v for k, v in report.items() if k != "detail"}, indent=2))


if __name__ == "__main__":
    main()

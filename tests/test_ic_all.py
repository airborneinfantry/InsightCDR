"""Full InsightCDR sweep on llmdev: clean insightcdr_test, then all suites.

The pre-sweep wipe drops and recreates the public schema (the insightcdr_app
role cannot TRUNCATE the postgres-owned tables), re-applies deploy/schema.sql,
then runs every suite in the canonical order — Task 7 found that leftover
fixture rows (call_id 300001 shared by test_ic_ingest and test_ic_calls) make
the suite order fragile on a dirty DB, so the sweep always starts clean.
"""
import os
import secrets
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(os.path.dirname(HERE), "deploy", "schema.sql")

SUITES = ["test_ic_scaffold.py", "test_ic_fieldmap.py", "test_ic_auth.py",
          "test_ic_web.py", "test_ic_ingest.py", "test_ic_calls.py", "test_ic_schema.py",
          "test_ic_dashboard.py", "test_ic_search.py", "test_ic_quality.py",
          "test_ic_utilization.py", "test_ic_reports.py"]

PSQL = ["sudo", "-n", "-u", "postgres", "psql", "-p", "5433", "-d", "insightcdr_test"]


def wipe_test_db():
    # PG15+ gives a manually (re)created schema no PUBLIC privileges, and
    # insightcdr_app must at least reach its tables: grant USAGE explicitly.
    subprocess.run(PSQL + ["-v", "ON_ERROR_STOP=1", "-c", "DROP SCHEMA public CASCADE",
                           "-c", "CREATE SCHEMA public",
                           "-c", "GRANT USAGE ON SCHEMA public TO insightcdr_app"], check=True)
    subprocess.run(PSQL + ["-v", "ON_ERROR_STOP=1", "-f", SCHEMA], check=True)


if __name__ == "__main__":
    if os.environ.get("IC_TEST_DSN", "").find("insightcdr_test") < 0:
        print("test_ic_all: refusing to run without IC_TEST_DSN pointing at insightcdr_test")
        sys.exit(1)
    # I-2: sweep self-sufficiency. app.config now fails fast (I-3) when
    # IC_DB_DSN / IC_SECRET_KEY are missing, and suites import app.main, so the
    # sweep must stand both up from IC_TEST_DSN alone before running the loop.
    if os.environ.get("IC_TEST_DSN"):
        os.environ.setdefault("IC_DB_DSN", os.environ["IC_TEST_DSN"])
        os.environ.setdefault("IC_SECRET_KEY", secrets.token_hex(32))
    wipe_test_db()
    for s in SUITES:
        r = subprocess.run([sys.executable, os.path.join(HERE, s)])
        if r.returncode != 0:
            print(f"FAILED: {s}")
            sys.exit(1)
    print("test_ic_all: OK")

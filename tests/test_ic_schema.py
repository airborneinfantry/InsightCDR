import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import psycopg
from tests.util import check

DSN = os.environ["IC_TEST_DSN"]

def run():
    conn = psycopg.connect(DSN, autocommit=True)
    tables = {r[0] for r in conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'").fetchall()}
    for t in ("cdr", "cmr", "calls", "device_daily", "device_roles", "users", "ingest_log"):
        check(t in tables, f"table {t} exists")
    pk = conn.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                      "WHERE conrelid='calls'::regclass AND contype='p'").fetchone()
    check("orig_call_leg_id" in pk[0], "calls PK includes orig_call_leg_id")
    check("dt_start" in pk[0], "calls PK includes dt_start (recycled call-id grain)")
    print("test_ic_schema: OK")

if __name__ == "__main__":
    run()

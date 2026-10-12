import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import datetime as dt
import psycopg
from app.routes.search import build_where, _normalize
from tests.util import check

DSN = os.environ["IC_TEST_DSN"]

def run():
    sql, params = build_where({"number": "55501234567", "disposition": "answered"})
    check("calling_party" in sql and "called_party" in sql, "number filter covers both sides")
    check(sql.count("%s") == len(params), "params match placeholders")
    # M-6: a date-only `to` filter is advanced to 23:59:59 so that day is
    # inclusive; datetime-local values and epoch passthrough stay exact.
    mid = dt.datetime(2026, 9, 10, tzinfo=dt.timezone.utc).timestamp()
    f = _normalize({"from_ts": "2026-09-01", "to_ts": "2026-09-10"})
    check(f["from_ts"] == str(int(dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc).timestamp())),
          "from date -> epoch midnight")
    check(f["to_ts"] == str(int(mid) + 86399), "date-only to_ts -> end of day inclusive")
    f2 = _normalize({"to_ts": "2026-09-10T12:30"})
    check(int(f2["to_ts"]) == int(mid) + 12 * 3600 + 30 * 60,
          "datetime-local to_ts stays exact (no end-of-day bump)")
    f3 = _normalize({"to_ts": str(int(mid))})
    check(f3["to_ts"] == str(int(mid)), "epoch to_ts passthrough untouched")
    where, wparams = build_where({"number": "55501234567"})
    conn = psycopg.connect(DSN, autocommit=True)
    row = conn.execute(f"SELECT count(*) FROM calls WHERE {where}", wparams).fetchone()
    check(row[0] >= 0, "search query executes")
    print("test_ic_search: OK")

if __name__ == "__main__":
    run()

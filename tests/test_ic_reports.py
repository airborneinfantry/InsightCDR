import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tests.util import bootstrap_env, check
bootstrap_env()  # I-2/I-3: IC_DB_DSN + random IC_SECRET_KEY before app.main import
import datetime as dt
import psycopg
import app.routes.reports  # noqa: F401  (Task 11 module must exist before routes render)
from app.main import app

DSN = os.environ["IC_TEST_DSN"]

# Fixture: five calls in Cluster-02 on 2026-09-08 (two hourly buckets), three
# devices across the quality/utilization fixtures. mos 2.5 is below the 3.5
# quality threshold; NULL mos rows exercise the AVG/SUM NULL handling.
CLUSTER = "Cluster-02"
DAY = dt.date(2026, 9, 8)
DAY_STR = DAY.isoformat()
DAY_END = (DAY + dt.timedelta(days=1)).isoformat()
DAY_BEFORE = (DAY - dt.timedelta(days=1)).isoformat()
BASE = int(dt.datetime(2026, 9, 8, 0, 0, tzinfo=dt.timezone.utc).timestamp())
DEV_A = "SEPREPTQA01"
DEV_B = "SEPREPTQB01"

# (call_id, hour, disposition, mos_best, orig_device)
FIX_CALLS = [
    (911201, 10, "answered", 4.1, DEV_A),
    (911202, 10, "answered", 3.9, DEV_B),
    (911203, 10, "failed", None, DEV_A),
    (911204, 14, "answered", 2.5, DEV_B),
    (911205, 14, "no-answer", None, DEV_A),
]

# (device_name, day_offset, call_count, total_duration, peak_hour, peak_hour_calls)
FIX_DD = [
    (DEV_A, 0, 10, 600, 9, 4),
    (DEV_A, 1, 20, 1800, 10, 8),
    (DEV_B, 0, 5, 300, 8, 2),
]


def _seed(conn):
    conn.execute("DELETE FROM calls WHERE cluster_id=%s AND call_id BETWEEN 911201 AND 911299",
                 (CLUSTER,))
    for call_id, hour, disp, mos, dev in FIX_CALLS:
        conn.execute(
            "INSERT INTO calls (cluster_id, call_id, call_manager_id, orig_call_leg_id, "
            "dt_start, calling_party, called_party, orig_device, dest_device, duration, "
            "disposition, mos_best) "
            "VALUES (%s, %s, 1, %s, %s, '1001', '1002', %s, 'SEP9999', 42, %s, %s)",
            (CLUSTER, call_id, call_id, BASE + hour * 3600 + 60, dev, disp, mos))
    for dev, off, calls, dur, peak_h, peak_n in FIX_DD:
        conn.execute(
            "INSERT INTO device_daily (cluster_id, device_name, day, role, call_count, "
            "total_duration, peak_hour, peak_hour_calls) VALUES (%s, %s, %s, 'phone', %s, %s, %s, %s)",
            (CLUSTER, dev, DAY + dt.timedelta(days=off), calls, dur, peak_h, peak_n))


def run():
    conn = psycopg.connect(DSN, autocommit=True)
    try:
        # Call-summary hourly-bucket query (brief Step 3 SQL) over the fixture.
        _seed(conn)
        rows = conn.execute(
            "SELECT EXTRACT(hour FROM to_timestamp(dt_start) AT TIME ZONE 'UTC')::int AS hr, "
            "COUNT(*), SUM((disposition='answered')::int), AVG(mos_best) "
            "FROM calls WHERE dt_start >= %s AND dt_start < %s GROUP BY 1 ORDER BY 1",
            (BASE, BASE + 86400)).fetchall()
        check(len(rows) == 2, "hourly buckets: two hours")
        check(rows[0][0] == 10 and rows[0][1] == 3 and rows[0][2] == 2, "hour 10 counts")
        check(abs(float(rows[0][3]) - 4.0) < 0.005, "hour 10 avg MOS 4.0")
        check(rows[1][0] == 14 and rows[1][1] == 2 and rows[1][2] == 1, "hour 14 counts")

        # Quality per-device query (brief Step 3 SQL) over the fixture range.
        qrows = conn.execute(
            "SELECT orig_device, COUNT(*), AVG(mos_best), SUM((mos_best<3.5)::int) "
            "FROM calls WHERE dt_start >= %s AND dt_start <= %s GROUP BY 1 ORDER BY 4 DESC NULLS LAST",
            (BASE - 86400, BASE + 2 * 86400)).fetchall()
        check(len(qrows) == 2, "quality: two devices")
        check(qrows[0][0] == DEV_B, "quality: device with low-MOS calls ranks first")
        check(qrows[0][3] == 1 and qrows[1][3] == 0, "quality: below-3.5 counts")
        check(abs(float(qrows[1][2]) - 4.1) < 0.005, "quality: avg MOS ignores NULLs")

        # Utilization per-device query (brief Step 3 SQL, M-8: cluster_id in the
        # GROUP BY) over device_daily.
        urows = conn.execute(
            "SELECT cluster_id, device_name, SUM(call_count), SUM(total_duration)/60, "
            "MAX(peak_hour), MAX(peak_hour_calls), COUNT(*) FROM device_daily "
            "WHERE day >= %s AND day <= %s GROUP BY 1, 2 ORDER BY 3 DESC",
            (DAY, DAY + dt.timedelta(days=1))).fetchall()
        check(len(urows) == 2, "utilization: two devices")
        check(urows[0][1] == DEV_A and urows[0][2] == 30, "utilization: calls summed across days")
        check(int(urows[0][3]) == 40, "utilization: minutes = duration/60")
        check(urows[0][4] == 10, "utilization: MAX(peak_hour)")

        # Routes via TestClient with a minted ic_session cookie (shell dependency).
        from fastapi.testclient import TestClient
        from app.main import app, serializer
        from app.auth import create_session
        c = TestClient(app)
        token = create_session(serializer, "tester", "admin")
        cookie = {"ic_session": token}

        r = c.get("/login")
        check(r.status_code == 200, "app boots with reports router wired")

        r = c.get("/reports/call-summary", params={"day": DAY_STR}, cookies=cookie)
        check(r.status_code == 200, "call-summary page renders")
        check("10:00" in r.text and "14:00" in r.text, "call-summary hourly rows present")
        check("4.00" in r.text and "3.50" in r.text, "call-summary per-hour and total avg MOS")
        check("@media print" in r.text and "topbar" in r.text, "call-summary print stylesheet")
        r = c.get("/reports/call-summary", params={"day": DAY_STR, "cluster": CLUSTER},
                  cookies=cookie)
        check(r.status_code == 200 and "14:00" in r.text, "call-summary cluster filter")
        r = c.get("/reports/call-summary", params={"day": DAY_STR, "cluster": "Cluster-99"},
                  cookies=cookie)
        check(r.status_code == 200 and "No calls on this day" in r.text,
              "call-summary empty state")
        r = c.get("/reports/call-summary", params={"day": "2026-13-40"}, cookies=cookie)
        check(r.status_code == 400, "unparseable day -> 400")
        r = c.get("/reports/call-summary", follow_redirects=False)
        check(r.status_code in (302, 303), "unauthenticated call-summary -> redirect")

        r = c.get("/reports/quality", params={"from": DAY_BEFORE, "to": "2026-09-09"},
                  cookies=cookie)
        check(r.status_code == 200, "quality report renders")
        check(DEV_A in r.text and DEV_B in r.text, "quality report shows fixture devices")
        check(r.text.find(DEV_B) < r.text.find(DEV_A), "quality report ordered by low-MOS desc")
        check("@media print" in r.text, "quality report print stylesheet")
        r = c.get("/reports/quality", params={"from": DAY_BEFORE, "to": "2026-09-09",
                                              "cluster": "Cluster-99"}, cookies=cookie)
        check(r.status_code == 200 and "No calls in this range" in r.text,
              "quality report empty state")
        r = c.get("/reports/quality", params={"from": "bad", "to": "2026-09-09"},
                  cookies=cookie)
        check(r.status_code == 400, "unparseable from -> 400")
        r = c.get("/reports/quality", follow_redirects=False)
        check(r.status_code in (302, 303), "unauthenticated quality -> redirect")

        r = c.get("/reports/utilization", params={"from": DAY_STR, "to": DAY_END},
                  cookies=cookie)
        check(r.status_code == 200, "utilization report renders")
        check(DEV_A in r.text and "40" in r.text, "utilization report shows minutes")
        check("@media print" in r.text, "utilization report print stylesheet")
        r = c.get("/reports/utilization", params={"from": "2026-09-08", "to": "bad"},
                  cookies=cookie)
        check(r.status_code == 400, "unparseable to -> 400")
        r = c.get("/reports/utilization", follow_redirects=False)
        check(r.status_code in (302, 303), "unauthenticated utilization -> redirect")

        r = c.get("/reports", follow_redirects=False)
        check(r.status_code in (302, 303), "/reports index redirects into the reports")
    finally:
        # Fixture cleanup so re-runs and later suites stay deterministic.
        conn.execute("DELETE FROM calls WHERE cluster_id=%s AND call_id BETWEEN 911201 AND 911299",
                     (CLUSTER,))
        conn.execute("DELETE FROM device_daily WHERE device_name IN (%s, %s)", (DEV_A, DEV_B))
        conn.close()
    print("test_ic_reports: OK")

if __name__ == "__main__":
    run()

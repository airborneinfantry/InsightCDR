import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import psycopg
from ingest.calls import rebuild_calls
from tests.util import check

DSN = os.environ["IC_TEST_DSN"]

# Two legs of one transferred call: same stitch key, different orig_call_leg_id.
# Leg 700 answered; leg 701 failed (no connect, cause 172).
LEGS = [
    ("Cluster-02", 300001, 1, 700, 1772000000, 1772000010, 1772000070, 0),
    ("Cluster-02", 300001, 1, 701, 1772000080, 0, 0, 172),
]

# Recycled call id: SAME stitch key AND leg id, two dt_orig 5 days apart —
# CUCM reuses globalCallID_callId, so these are two distinct real calls.
# The second instance stores dt_connect=0 (the prod CUCM no-answer shape,
# not NULL) so the zero path of the duration CASE has fixture coverage.
RECYCLED = [
    ("Cluster-02", 300002, 1, 700, 1772000000, 1772000010, 1772000070, 0),
    ("Cluster-02", 300002, 1, 700, 1772432000, 0, 1772432040, 0),
]

def run():
    conn = psycopg.connect(DSN, autocommit=True)
    for cluster, cid, cmid, leg, d0, dc, dd, cause in LEGS + RECYCLED:
        conn.execute(
            "DELETE FROM cdr WHERE cluster_id=%s AND call_id=%s AND call_manager_id=%s "
            "AND COALESCE(orig_call_leg_id,0)=%s AND dt_orig=%s", (cluster, cid, cmid, leg, d0))
    with conn.cursor() as cur:
        with cur.copy("COPY cdr (cluster_id, call_id, call_manager_id, dt_orig, dt_connect, "
                      "dt_disc, cause_code, orig_call_leg_id, raw, filename) FROM STDIN") as cp:
            for cluster, cid, cmid, leg, d0, dc, dd, cause in LEGS + RECYCLED:
                # Store 0 as 0 (prod shape), not NULL: exercises the
                # dt_connect=0 / dt_disc=0 paths of the duration CASE.
                cp.write_row((cluster, cid, cmid, d0, dc, dd, cause, leg, '{}',
                              'synthetic_CDR.txt'))
    rebuild_calls(conn, [("Cluster-02", 300001, 1), ("Cluster-02", 300002, 1)])
    rows = conn.execute(
        "SELECT orig_call_leg_id, dt_start, disposition, duration FROM calls "
        "WHERE cluster_id='Cluster-02' AND call_id IN (300001, 300002) "
        "ORDER BY call_id, dt_start").fetchall()
    check(len(rows) == 4, "two legs + two recycled instances -> four calls rows")
    check(rows[0][1] == 1772000000 and rows[1][1] == 1772000080, "leg dt_start = its dt_orig")
    check(rows[2][1] != rows[3][1], "recycled same-leg instances stay distinct rows")
    check(rows[2][1] == 1772000000 and rows[3][1] == 1772432000, "recycled dt_start set")
    check(rows[0][2] == "answered" and rows[1][2] == "failed", "disposition per leg")
    # I-1: answered leg = disc - connect = 60s; the no-connect failed leg must
    # be 0 seconds, never the raw disconnect epoch.
    check(rows[0][3] == 60, "answered leg duration 60s")
    check(rows[1][3] == 0, "no-connect leg duration 0s (not the disconnect epoch)")
    # rows[2] = answered instance; rows[3] = the dt_connect=0 (prod shape)
    # instance: must also be 0, never the disconnect epoch (1.77e9).
    check(rows[2][3] == 60, "answered recycled instance duration 60s")
    check(rows[3][3] == 0, "dt_connect=0 instance duration 0s (zero path, not epoch)")
    rebuild_calls(conn, [("Cluster-02", 300001, 1), ("Cluster-02", 300002, 1)])
    cnt = conn.execute("SELECT count(*) FROM calls WHERE cluster_id='Cluster-02' "
                       "AND call_id IN (300001, 300002)").fetchone()[0]
    check(cnt == 4, "rebuild upserts without duplicating")
    # Cleanup removes only the recycled fixture (300002). The 300001 rows are
    # shared with test_ic_ingest's fixture and stay: the dashboard suite's
    # "KPI query returns rows" check consumes that materialized calls row.
    conn.execute("DELETE FROM cdr WHERE cluster_id='Cluster-02' AND call_id=300002")
    conn.execute("DELETE FROM calls WHERE cluster_id='Cluster-02' AND call_id=300002")
    print("test_ic_calls: OK")

if __name__ == "__main__":
    run()

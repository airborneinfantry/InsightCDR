import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tests.util import bootstrap_env, check
bootstrap_env()  # I-2/I-3: IC_DB_DSN + random IC_SECRET_KEY before app.main import
import psycopg
import app.routes.quality  # noqa: F401  (Task 9 module must exist before routes render)

DSN = os.environ["IC_TEST_DSN"]

# Fixture: one call (stitch key Cluster-02 / 910001 / 1) with mos_best 2.5 and
# two CMR legs on two different UTC days for the per-device history page.
DEVICE = "SEPQUALITYTEST01"
FIXTURE_CMR = [
    ("Cluster-02", 910001, 1, DEVICE, 1772000000, 10, 20, 30, 3.10, 3.20),
    ("Cluster-02", 910001, 1, DEVICE, 1772600000, 40, 60, 90, 2.90, 3.00),
]

def _seed(conn):
    conn.execute("DELETE FROM cmr WHERE cluster_id='Cluster-02' AND call_id=910001")
    conn.execute("DELETE FROM calls WHERE cluster_id='Cluster-02' AND call_id=910001")
    conn.execute(
        "INSERT INTO calls (cluster_id, call_id, call_manager_id, orig_call_leg_id, dt_start, "
        "calling_party, called_party, orig_device, dest_device, duration, disposition, mos_best) "
        "VALUES ('Cluster-02', 910001, 1, 500, 1772000000, '1001', '1002', %s, "
        "'SEP9999', 42, 'answered', 2.5)", (DEVICE,))
    with conn.cursor() as cur:
        with cur.copy("COPY cmr (cluster_id, call_id, call_manager_id, device_name, dt, "
                      "packets_lost, jitter, latency, mos_lqo, mos_cqo, raw, filename) "
                      "FROM STDIN") as cp:
            for row in FIXTURE_CMR:
                cp.write_row(row + ("{}", "synthetic_CMR.txt"))

def run():
    conn = psycopg.connect(DSN, autocommit=True)
    rows = conn.execute(
        "SELECT c.call_id, c.mos_best, m.packets_lost, m.jitter, m.latency "
        "FROM calls c JOIN cmr m ON m.cluster_id=c.cluster_id AND m.call_id=c.call_id "
        "AND m.call_manager_id=c.call_manager_id "
        "WHERE c.mos_best IS NOT NULL ORDER BY c.mos_best ASC NULLS LAST LIMIT 5").fetchall()
    check(len(rows) >= 0, "worst-calls query executes")

    # Per-device daily aggregation (brief Step 3 SQL) over seeded fixture legs.
    _seed(conn)
    hist = conn.execute(
        "SELECT (to_timestamp(dt) AT TIME ZONE 'UTC')::date AS day, COUNT(*), AVG(mos_lqo), "
        "SUM(packets_lost), AVG(jitter), AVG(latency) "
        "FROM cmr WHERE device_name=%s GROUP BY 1 ORDER BY 1 DESC", (DEVICE,)).fetchall()
    check(len(hist) == 2, "device history aggregates two days")
    check(abs(float(hist[0][2]) - 2.9) < 0.005, "per-day avg MOS (latest day)")
    check(hist[0][3] == 40, "per-day packet-loss sum")
    check(hist[1][3] == 10, "per-day packet-loss sum (older day)")

    # Routes via TestClient with a minted ic_session cookie (shell dependency).
    from fastapi.testclient import TestClient
    from app.main import app, serializer
    from app.auth import create_session
    c = TestClient(app)
    token = create_session(serializer, "tester", "admin")
    r = c.get("/quality/worst", cookies={"ic_session": token})
    check(r.status_code == 200, "worst page renders")
    check("Worst calls" in r.text, "worst page heading")
    check("2.50" in r.text and DEVICE in r.text, "worst page shows fixture call + device link")
    r = c.get("/quality/worst", params={"mos_max": "3.5"}, cookies={"ic_session": token})
    check(r.status_code == 200, "worst page accepts mos_max filter")
    r = c.get("/quality/worst", params={"mos_max": "1.0"}, cookies={"ic_session": token})
    check(r.status_code == 200 and "No calls with MOS data" in r.text, "worst page empty state")
    r = c.get("/quality/worst", params={"mos_max": "abc"}, cookies={"ic_session": token})
    check(r.status_code == 400, "unparseable mos_max -> 400")
    r = c.get("/quality/device/" + DEVICE, cookies={"ic_session": token})
    check(r.status_code == 200, "device history page renders")
    check("3.10" in r.text and "2.90" in r.text, "device page renders per-day MOS values")
    r = c.get("/quality/device/SEPNO_SUCH_DEVICE_000", cookies={"ic_session": token})
    check(r.status_code == 200 and "No CMR quality records" in r.text,
          "unknown device renders empty state")
    r = c.get("/quality/worst", follow_redirects=False)
    check(r.status_code in (302, 303), "unauthenticated -> redirect to /login")

    # Fixture cleanup so re-runs and later suites stay deterministic.
    conn.execute("DELETE FROM cmr WHERE device_name=%s", (DEVICE,))
    conn.execute("DELETE FROM calls WHERE cluster_id='Cluster-02' AND call_id=910001")
    print("test_ic_quality: OK")

if __name__ == "__main__":
    run()

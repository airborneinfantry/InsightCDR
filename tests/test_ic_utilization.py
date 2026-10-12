import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tests.util import bootstrap_env, check
bootstrap_env()  # I-2/I-3: IC_DB_DSN + random IC_SECRET_KEY before app.main import
import datetime as dtd
import psycopg
import app.routes.utilization  # noqa: F401  (Task 10 module must exist before routes render)
from app.routes.utilization import seed_device_roles

DSN = os.environ["IC_TEST_DSN"]

# Fixture devices chosen so each name-pattern rule in seed_device_roles fires once.
PH = "SEPUTILTEST01"     # SEP% -> phone
CU = "CUBEUTILTEST01"    # %CUBE% -> cube
TR = "SIPTRUNKUTIL01"    # (?i)(trunk|sip) -> trunk
GW = "VGWUTILTEST01"     # (?i)(vg|gw|gateway) -> gateway
OT = "OTHRUTILTEST01"    # no pattern -> other
DEVICES = [PH, CU, TR, GW, OT]
EXPECT_ROLE = {PH: "phone", CU: "cube", TR: "trunk", GW: "gateway", OT: "other"}

D1 = dtd.datetime.fromtimestamp(1772000000, dtd.timezone.utc).date()
D2 = dtd.datetime.fromtimestamp(1772600000, dtd.timezone.utc).date()

FIX_CALLS = [
    # one calls row per fixture device so the seed's UNION DISTINCT scan sees each name
    ("Cluster-02", 912001, 1, 601, 1772000000, "1001", "1002", PH, "SEP9999", 60, "answered"),
    ("Cluster-02", 912002, 1, 602, 1772000000, "1001", "1002", CU, "SEP9999", 60, "answered"),
    ("Cluster-02", 912003, 1, 603, 1772000000, "1001", "1002", TR, "SEP9999", 60, "answered"),
    ("Cluster-02", 912004, 1, 604, 1772000000, "1001", "1002", GW, "SEP9999", 60, "answered"),
    ("Cluster-02", 912005, 1, 605, 1772000000, "1001", "1002", OT, "SEP9999", 60, "answered"),
]

FIX_DD = [
    ("Cluster-02", PH, D1, 10, 6060, 14, 3),
    ("Cluster-02", PH, D2, 20, 12120, 15, 5),
    ("Cluster-02", CU, D1, 5, 300, 12, 2),
]


def _cleanup(conn):
    conn.execute("DELETE FROM device_daily WHERE device_name = ANY(%s)", (DEVICES,))
    conn.execute("DELETE FROM calls WHERE orig_device = ANY(%s) OR dest_device = ANY(%s)",
                 (DEVICES, DEVICES))
    conn.execute("DELETE FROM device_roles WHERE device_name = ANY(%s)",
                 (DEVICES + ["SEP0A1B2C3D4E5F"],))


def run():
    conn = psycopg.connect(DSN, autocommit=True)
    rows = conn.execute(
        "SELECT device_name, day, call_count, total_duration, peak_hour, role "
        "FROM device_daily ORDER BY total_duration DESC LIMIT 5").fetchall()
    check(len(rows) >= 0, "utilization query executes")
    conn.execute("INSERT INTO device_roles (device_name, role, updated_by) VALUES (%s,%s,%s) "
                 "ON CONFLICT (device_name) DO UPDATE SET role=EXCLUDED.role, updated_by=EXCLUDED.updated_by",
                 ("SEP0A1B2C3D4E5F", "phone", "tester"))
    got = conn.execute("SELECT role FROM device_roles WHERE device_name='SEP0A1B2C3D4E5F'").fetchone()
    check(got[0] == "phone", "device role upsert")

    _cleanup(conn)
    for row in FIX_CALLS:
        conn.execute("INSERT INTO calls (cluster_id, call_id, call_manager_id, orig_call_leg_id, "
                     "dt_start, calling_party, called_party, orig_device, dest_device, duration, "
                     "disposition) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", row)
    conn.execute("DELETE FROM device_roles")  # seed heuristic only fires when the table is empty

    # Routes via TestClient with a minted ic_session cookie (shell dependency).
    from fastapi.testclient import TestClient
    from app.main import app, serializer
    from app.auth import create_session
    c = TestClient(app)
    token = create_session(serializer, "tester", "admin")
    ck = {"ic_session": token}

    # First utilization render must seed device_roles from the calls name patterns.
    r = c.get("/utilization", cookies=ck)
    check(r.status_code == 200, "utilization page renders")
    seeded = dict(conn.execute("SELECT device_name, role FROM device_roles").fetchall())
    for dev in DEVICES:
        check(seeded.get(dev) == EXPECT_ROLE[dev], f"seeded role {dev}={EXPECT_ROLE[dev]}")
    check(seeded.get(PH) == "phone" and seeded.get(CU) == "cube", "seed heuristic by name pattern")

    # Count guard: a second pass must not touch existing mappings.
    n_before = conn.execute("SELECT count(*) FROM device_roles").fetchone()[0]
    seed_device_roles(conn, updated_by="other-user")
    n_after = conn.execute("SELECT count(*) FROM device_roles").fetchone()[0]
    check(n_before == n_after, "seed count guard: no re-seed when non-empty")
    ub = conn.execute("SELECT updated_by FROM device_roles WHERE device_name=%s", (PH,)).fetchone()[0]
    check(ub == "tester", "route seed records acting user as updated_by")

    # Utilization table over device_daily fixture data.
    for row in FIX_DD:
        conn.execute("INSERT INTO device_daily (cluster_id, device_name, day, call_count, "
                     "total_duration, peak_hour, peak_hour_calls, role) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                     row + ("other",))
    r = c.get("/utilization", cookies=ck)
    check(r.status_code == 200 and PH in r.text and CU in r.text,
          "utilization table shows fixture devices")
    check("30" in r.text, "utilization aggregates calls across days (10+20)")
    check('class="trend"' in r.text, "per-role SVG trend present")
    r = c.get("/utilization", params={"role": "phone"}, cookies=ck)
    check(r.status_code == 200 and PH in r.text and CU not in r.text,
          "role filter narrows to phone devices")
    r = c.get("/utilization", params={"from": D2.isoformat(), "to": D2.isoformat()}, cookies=ck)
    check(r.status_code == 200 and CU not in r.text and PH in r.text,
          "date range filter narrows device set")
    r = c.get("/utilization", params={"from": "not-a-date"}, cookies=ck)
    check(r.status_code == 400, "unparseable from date -> 400")

    # Per-device drill-down.
    r = c.get("/utilization/device/" + PH, cookies=ck)
    check(r.status_code == 200 and "101" in r.text and "202" in r.text,
          "device drill-down renders per-day minutes")
    check('class="trend"' in r.text, "device drill-down trend present")
    r = c.get("/utilization/device/SEPNO_SUCH_DEVICE_000", cookies=ck)
    check(r.status_code == 200 and "No utilization data" in r.text,
          "unknown device renders empty state")

    # Roles CRUD.
    r = c.get("/utilization/roles", cookies=ck)
    check(r.status_code == 200 and CU in r.text, "roles page lists mappings")
    r = c.post("/utilization/roles", data={"device_name": CU, "role": "phone"},
               cookies=ck, follow_redirects=False)
    check(r.status_code in (303, 302), "role upsert redirects")
    row = conn.execute("SELECT role, updated_by FROM device_roles WHERE device_name=%s",
                       (CU,)).fetchone()
    check(row == ("phone", "tester"), "POST upserts role with acting user")
    r = c.get("/utilization/roles", cookies=ck)
    check("phone" in r.text, "roles page reflects the upsert")
    r = c.post("/utilization/roles", data={"device_name": CU, "role": "not-a-role"}, cookies=ck)
    check(r.status_code == 400, "invalid role -> 400")
    r = c.post("/utilization/roles", data={"device_name": "  ", "role": "phone"}, cookies=ck)
    check(r.status_code == 400, "blank device name -> 400")

    r = c.get("/utilization", follow_redirects=False)
    check(r.status_code in (302, 303), "unauthenticated -> redirect to /login")

    _cleanup(conn)
    print("test_ic_utilization: OK")

if __name__ == "__main__":
    run()

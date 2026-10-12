import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tests.util import bootstrap_env, check
bootstrap_env()  # I-2/I-3: IC_DB_DSN + random IC_SECRET_KEY before app.main import
import psycopg
from itsdangerous import URLSafeTimedSerializer
from app.auth import hash_password, verify_password, create_session, read_session

DSN = os.environ["IC_TEST_DSN"]

def run():
    h = hash_password("hunter2")
    check(verify_password("hunter2", h), "argon2 verify ok")
    check(not verify_password("wrong", h), "argon2 reject wrong")
    s = URLSafeTimedSerializer("k")
    tok = create_session(s, "alice", "analyst")
    claim = read_session(s, tok, ttl=3600)
    check(claim == {"u": "alice", "r": "analyst"},
          "session claim carries the role")
    check(read_session(s, tok + "x", ttl=3600) is None, "tampered token rejected")

    # Login flow end-to-end: seeded creds -> POST /login -> 303 + usable cookie,
    # and the claim's role decides what require() lets through.
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    conn = psycopg.connect(DSN, autocommit=True)
    try:
        conn.execute("DELETE FROM users WHERE username IN ('t_auth_admin', 't_auth_viewer')")
        conn.execute(
            "INSERT INTO users (username, password_hash, role) "
            "VALUES (%s, %s, 'admin'), (%s, %s, 'viewer')",
            ("t_auth_admin", hash_password("pw-admin-1"),
             "t_auth_viewer", hash_password("pw-viewer-1")))
        r = c.post("/login", data={"username": "t_auth_admin", "password": "pw-admin-1"},
                   follow_redirects=False)
        check(r.status_code == 303 and "ic_session" in r.cookies, "seeded login -> 303 + cookie")
        admin_cookie = r.cookies
        r = c.get("/reports", follow_redirects=False, cookies=admin_cookie)
        check(r.status_code == 303, "admin reaches /reports (redirects into the reports)")
        r = c.post("/login", data={"username": "t_auth_viewer", "password": "pw-viewer-1"},
                   follow_redirects=False)
        check(r.status_code == 303, "viewer login ok")
        vcookie = r.cookies
        r = c.get("/reports/call-summary", cookies=vcookie)
        check(r.status_code == 403, "viewer blocked from /reports (403)")
        r = c.get("/dashboard", cookies=vcookie)
        check(r.status_code == 200, "viewer can reach /dashboard")
    finally:
        conn.execute("DELETE FROM users WHERE username IN ('t_auth_admin', 't_auth_viewer')")
        conn.close()
    print("test_ic_auth: OK")

if __name__ == "__main__":
    run()

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tests.util import bootstrap_env, check
bootstrap_env()  # I-2/I-3: IC_DB_DSN + random IC_SECRET_KEY before app.main import
from fastapi.testclient import TestClient
from app.main import app

def run():
    c = TestClient(app)
    r = c.get("/", follow_redirects=False)
    check(r.status_code in (302, 303), "unauthenticated -> redirect to /login")
    r = c.get("/login")
    check(r.status_code == 200 and "InsightCDR" in r.text, "login page renders")
    print("test_ic_web: OK")

if __name__ == "__main__":
    run()
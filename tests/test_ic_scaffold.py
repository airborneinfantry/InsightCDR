# tests/test_ic_scaffold.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.config import load_config
from tests.util import check

def run():
    cfg = load_config({
        "IC_DB_DSN": "postgresql://insightcdr_app:pw@127.0.0.1:5433/insightcdr",
        "IC_SECRET_KEY": "s3cret",
        "IC_ADMIN_PASSWORD": "bootpass",
    })
    check(cfg["db_dsn"] == "postgresql://insightcdr_app:pw@127.0.0.1:5433/insightcdr", "dsn")
    check(cfg["app_port"] == 8011, "app_port default 8011")
    check(cfg["session_ttl"] == 43200, "session_ttl default 12h")
    check(cfg["spool"].endswith("/opt/insightcdr/spool/incoming"), "spool default")
    # I-3 fail-fast: the dev placeholder secret must raise even when the
    # process env carries a real key (the env dict wins), and the explicit
    # IC_ALLOW_INSECURE_DEFAULT=1 opt-out must short-circuit the checks.
    try:
        load_config({"IC_DB_DSN": "postgresql://x", "IC_SECRET_KEY": "dev-secret-change-me"})
        ok = False
    except RuntimeError:
        ok = True
    check(ok, "dev placeholder IC_SECRET_KEY -> RuntimeError")
    cfg2 = load_config({"IC_ALLOW_INSECURE_DEFAULT": "1"})  # must not raise
    check(cfg2["session_ttl"] == 43200, "IC_ALLOW_INSECURE_DEFAULT=1 short-circuits fail-fast")
    print("test_ic_scaffold: OK")

if __name__ == "__main__":
    run()
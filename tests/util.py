# tests/util.py
import os
import secrets


def bootstrap_env():
    """I-2/I-3 companion: app.config now fails fast at load time when
    IC_DB_DSN is unset or IC_SECRET_KEY is unset/dev-placeholder, so any suite
    that imports app.main (directly or via a route) must be able to stand both
    up from IC_TEST_DSN alone. Suites call this before importing app.main;
    test_ic_all's sweep sets the same defaults itself before the suite loop.
    """
    if os.environ.get("IC_TEST_DSN"):
        os.environ.setdefault("IC_DB_DSN", os.environ["IC_TEST_DSN"])
    os.environ.setdefault("IC_SECRET_KEY", secrets.token_hex(32))


def check(cond, label):
    if cond:
        print(f"  ok: {label}")
    else:
        print(f"  FAIL: {label}")
        raise SystemExit(1)

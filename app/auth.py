from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from itsdangerous import URLSafeTimedSerializer, BadSignature

_ph = PasswordHasher()


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, h: str) -> bool:
    try:
        _ph.verify(h, pw)
        return True
    except VerifyMismatchError:
        return False


def create_session(serializer, username: str, role: str) -> str:
    return serializer.dumps({"u": username, "r": role})


def read_session(serializer, token: str, ttl: int):
    try:
        return serializer.loads(token, max_age=ttl)
    except BadSignature:
        return None


def seed_admin(cfg):
    """Insert the default admin from .env at first run (spec §7) — no-op if exists."""
    if not cfg.get("admin_password"):
        return
    from app.db import get_conn
    conn = get_conn(cfg)
    try:
        if conn.execute("SELECT 1 FROM users WHERE username=%s", (cfg["admin_user"],)).fetchone():
            return
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (%s,%s,'admin') "
            "ON CONFLICT (username) DO NOTHING",
            (cfg["admin_user"], hash_password(cfg["admin_password"])))
    finally:
        conn.close()

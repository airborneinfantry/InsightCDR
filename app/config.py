import os

DEFAULTS = {
    "app_port": 8011,
    "session_ttl": 43200,
    "spool": "/opt/insightcdr/spool/incoming",
    "backup_dir": "/opt/insightcdr/data/backups",
}

# Dev placeholder historically baked in as the default secret — must never
# reach production (I-3 fail-fast).
DEV_SECRET_PLACEHOLDER = "dev-secret-change-me"


def _opt_out(env: dict) -> bool:
    return (env.get("IC_ALLOW_INSECURE_DEFAULT")
            or os.environ.get("IC_ALLOW_INSECURE_DEFAULT") or "") == "1"


def load_config(env: dict) -> dict:
    cfg = dict(DEFAULTS)
    cfg["db_dsn"] = env.get("IC_DB_DSN") or os.environ.get("IC_DB_DSN") or ""
    cfg["secret_key"] = env.get("IC_SECRET_KEY") or os.environ.get("IC_SECRET_KEY") or ""
    cfg["admin_user"] = env.get("IC_ADMIN_USER") or os.environ.get("IC_ADMIN_USER") or "admin"
    cfg["admin_password"] = env.get("IC_ADMIN_PASSWORD") or os.environ.get("IC_ADMIN_PASSWORD") or ""
    cfg["app_port"] = int(env.get("IC_APP_PORT") or cfg["app_port"])
    cfg["session_ttl"] = int(env.get("IC_SESSION_TTL") or cfg["session_ttl"])
    if env.get("IC_SPOOL"):
        cfg["spool"] = env["IC_SPOOL"]
    # Fail fast at load time rather than failing later at first use (I-3).
    # IC_ALLOW_INSECURE_DEFAULT=1 is the explicit dev/test opt-out.
    if _opt_out(env):
        return cfg
    if not cfg["db_dsn"]:
        raise RuntimeError(
            "IC_DB_DSN is not set: export it (postgresql://user:pass@host:5433/db) "
            "or set IC_ALLOW_INSECURE_DEFAULT=1 for a dev/test run")
    if not cfg["secret_key"]:
        raise RuntimeError(
            "IC_SECRET_KEY is not set: generate one with `python3 -c "
            "\"import secrets; print(secrets.token_hex(32))\"` "
            "or set IC_ALLOW_INSECURE_DEFAULT=1 for a dev/test run")
    if cfg["secret_key"] == DEV_SECRET_PLACEHOLDER:
        raise RuntimeError(
            "IC_SECRET_KEY still equals the dev placeholder: replace it with "
            "`python3 -c \"import secrets; print(secrets.token_hex(32))\"`")
    return cfg
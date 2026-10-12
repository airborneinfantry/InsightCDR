# InsightCDR

Standalone CDR/CMR portal for CUCM call records: FastAPI + Jinja2 + psycopg 3
against the :5433 PostgreSQL instance on llmdev (databases `insightcdr` prod +
`insightcdr_test`), with an SFTP-fed spool ingest worker and nightly backups.

## Layout

- `app/` — FastAPI app (auth, routes, templates)
- `ingest/` — spool worker, loader, calls/device_daily rollup, corpus seeder
- `deploy/` — schema.sql, systemd units, Caddy site, db/sftp setup scripts
- `tests/` — plain-script suites; `test_ic_all.py` is the 12-suite sweep

## Test sweep (on llmdev)

    IC_TEST_DSN="postgresql://insightcdr_app:<pw>@127.0.0.1:5433/insightcdr_test" \
      python3 insightcdr/tests/test_ic_all.py

The sweep needs ONLY IC_TEST_DSN: it derives IC_DB_DSN from it and mints a
random IC_SECRET_KEY for the run (see below), wipes insightcdr_test, then runs
every suite in the canonical order.

## Fresh-host deploy prerequisites (M-7)

`deploy.sh` syncs the code tree and restarts the systemd units, but a brand-new
host needs these MANUAL steps first — do not assume the script covers them:

1. **config/.env is NOT synced by deploy.sh** (excluded from both rsync passes
   so server-only secrets survive). Create `/opt/insightcdr/config/.env` by
   hand from `.env.example`; set IC_DB_DSN, IC_SECRET_KEY (generate with
   `python3 -c "import secrets; print(secrets.token_hex(32))"`), IC_ADMIN_USER
   /IC_ADMIN_PASSWORD. Since the I-3 fail-fast, the app refuses to boot without
   a real IC_DB_DSN and a non-placeholder IC_SECRET_KEY
   (IC_ALLOW_INSECURE_DEFAULT=1 is the explicit dev opt-out).
2. **data/backups must exist** before the backup unit runs:
   `sudo mkdir -p /opt/insightcdr/data/backups && sudo chown llmadmin /opt/insightcdr/data/backups`
3. **db_setup.sh and sftp_setup.sh are manual first-boot steps**:
   `sudo -u postgres bash deploy/db_setup.sh <app_password>` creates the
   insightcdr role, the insightcdr/insightcdr_test databases and applies
   schema.sql; `sudo bash deploy/sftp_setup.sh` creates the insightcdr-cdr SFTP
   chroot user. Run them once, before the first deploy.
4. **The backup unit relies on ~/.pgpass**: insightcdr-backup.service runs
   `pg_dump -h 127.0.0.1 -p 5433 -U insightcdr_app insightcdr` as llmadmin with
   no password on the command line, so
   `~/.pgpass` (chmod 600) must contain
   `127.0.0.1:5433:insightcdr:insightcdr_app:<app_password>`.
5. The nightly backup writes `/opt/insightcdr/data/backups/insightcdr-<date>.sql.gz`
   and keeps the newest 14.

## Prod re-roll (calls + device_daily rebuild from cdr)

    cd /opt/insightcdr && IC_DB_DSN="postgresql://insightcdr_app:<pw>@127.0.0.1:5433/insightcdr" \
      python3 -c "
from app.db import get_conn
from app.config import load_config
import os
from ingest.seed import rebuild_calls_all
from ingest.daily import refresh_device_daily_batch
conn = get_conn(load_config(dict(os.environ)))
conn.execute('TRUNCATE calls, device_daily')
rebuild_calls_all(conn)
refresh_device_daily_batch(conn)
print('re-roll done')"

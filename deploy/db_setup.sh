#!/usr/bin/env bash
# Creates the DB + roles for InsightCDR on the :5433 PostgreSQL instance.
# Run: sudo -u postgres bash db_setup.sh <app_password>
# NOTE: every psql call pins -p 5433. Without it psql defaults to the
# :5432 main cluster (sudo strips PGPORT from the environment), which
# silently created the DB on the wrong instance during first run.
set -euo pipefail
PW="${1:?usage: db_setup.sh <app_password>}"
psql -p 5433 -v ON_ERROR_STOP=1 <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'insightcdr_app') THEN
    CREATE ROLE insightcdr_app LOGIN PASSWORD '$PW';
  END IF;
END \$\$;
SELECT 'CREATE DATABASE insightcdr OWNER insightcdr_app'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'insightcdr')\gexec
SQL
PGPORT=5433 PGPASSWORD="$PW" psql -h 127.0.0.1 -U insightcdr_app -d insightcdr -f /opt/insightcdr/deploy/schema.sql

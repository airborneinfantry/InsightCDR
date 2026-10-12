-- Role insightcdr_app is created by db_setup.sh BEFORE this file runs.
-- Unlike Blue Marmot, InsightCDR is operational: UPDATE/DELETE allowed
-- (calls/device_daily are derived and rebuilt; cdr/cmr are insert-only in practice).

CREATE TABLE cdr (
  id bigserial PRIMARY KEY,
  cluster_id text NOT NULL,
  call_id bigint NOT NULL,
  call_manager_id int NOT NULL,
  dt_orig bigint NOT NULL,
  dt_connect bigint,
  dt_disc bigint,
  calling_party text,
  called_party text,
  orig_partition text,
  dest_partition text,
  orig_user_id text,
  dest_user_id text,
  orig_device text,
  dest_device text,
  duration int,
  cause_code int,
  orig_call_leg_id bigint,
  raw jsonb NOT NULL,
  filename text NOT NULL
);
CREATE INDEX cdr_time_idx ON cdr (dt_orig);
CREATE INDEX cdr_stitch_idx ON cdr (cluster_id, call_id, call_manager_id);
CREATE INDEX cdr_calling_idx ON cdr (calling_party);
CREATE INDEX cdr_user_idx ON cdr (orig_user_id);

CREATE TABLE cmr (
  id bigserial PRIMARY KEY,
  cluster_id text NOT NULL,
  call_id bigint NOT NULL,
  call_manager_id int NOT NULL,
  device_name text,
  dt bigint,
  packets_sent int, packets_recv int, packets_lost int,
  octets_sent bigint, octets_recv bigint,
  jitter int, latency int,
  mos_lqo real, mos_cqo real,
  raw jsonb NOT NULL,
  filename text NOT NULL
);
CREATE INDEX cmr_stitch_idx ON cmr (cluster_id, call_id, call_manager_id);
CREATE INDEX cmr_device_idx ON cmr (device_name, dt);

-- Materialized 1-row-per-call-instance query surface (spec §4). Rebuilt by ingest.
-- dt_start is part of the identity: CUCM recycles globalCallID_callId (and leg ids
-- within it), so two real calls can share (cluster, call_id, cm, orig_leg) at
-- different times. The per-row dt_start (= MIN(dt_orig) of that CDR instance)
-- keeps recycled instances distinct; without it ~6 real calls collapse into 1.
CREATE TABLE calls (
  cluster_id text NOT NULL,
  call_id bigint NOT NULL,
  call_manager_id int NOT NULL,
  orig_call_leg_id bigint NOT NULL DEFAULT 0,
  dt_start bigint NOT NULL,
  dt_connect bigint,
  dt_disc bigint,
  calling_party text,
  called_party text,
  orig_partition text,
  dest_partition text,
  orig_user_id text,
  orig_device text,
  dest_device text,
  duration int,
  disposition text NOT NULL,          -- answered | no-answer | failed
  cause_code int,
  mos_best real, mos_avg real,
  leg_count int,
  PRIMARY KEY (cluster_id, call_id, call_manager_id, orig_call_leg_id, dt_start)
);
CREATE INDEX calls_time_idx ON calls (dt_start);
CREATE INDEX calls_calling_idx ON calls (calling_party);
CREATE INDEX calls_called_idx ON calls (called_party);
CREATE INDEX calls_origdev_idx ON calls (orig_device);
CREATE INDEX calls_destdev_idx ON calls (dest_device);
CREATE INDEX calls_user_idx ON calls (orig_user_id);
CREATE INDEX calls_cluster_idx ON calls (cluster_id);
CREATE INDEX calls_mos_idx ON calls (mos_best);

-- Utilization rollup (spec §4), rebuilt for affected (cluster, day) pairs after ingest.
CREATE TABLE device_daily (
  cluster_id text NOT NULL,
  device_name text NOT NULL,
  day date NOT NULL,
  role text NOT NULL DEFAULT 'other',
  call_count int NOT NULL,
  total_duration bigint NOT NULL,
  peak_hour smallint,
  peak_hour_calls int,
  PRIMARY KEY (cluster_id, device_name, day)
);
CREATE INDEX device_daily_day_idx ON device_daily (day);

-- UI-managed device-name -> role mapping (spec §6, utilization dashboard).
CREATE TABLE device_roles (
  device_name text PRIMARY KEY,
  role text NOT NULL CHECK (role IN ('trunk', 'gateway', 'cube', 'phone', 'other')),
  updated_by text,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
  username text PRIMARY KEY,
  password_hash text NOT NULL,
  role text NOT NULL CHECK (role IN ('admin', 'analyst', 'viewer')),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ingest_log (
  id bigserial PRIMARY KEY,
  filename text UNIQUE NOT NULL,
  kind text,
  bytes bigint,
  rows_loaded int,
  status text NOT NULL,
  error text,
  loaded_at timestamptz NOT NULL DEFAULT now()
);

GRANT SELECT, INSERT, UPDATE, DELETE ON cdr, cmr, calls, device_daily, device_roles, users, ingest_log TO insightcdr_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO insightcdr_app;  -- bigserial sequences (cdr_id_seq etc.); added by Task 4 after live tests hit "permission denied for sequence cdr_id_seq"

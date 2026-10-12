DAILY_SQL = """
DELETE FROM device_daily d USING _pairs p
  WHERE d.cluster_id = p.cluster_id AND d.day = p.day;

INSERT INTO device_daily (cluster_id, device_name, day, role, call_count,
                          total_duration, peak_hour, peak_hour_calls)
WITH sides AS (
  SELECT c.cluster_id, x.device_name,
         (to_timestamp(c.dt_orig) AT TIME ZONE 'UTC')::date AS day,
         EXTRACT(hour FROM to_timestamp(c.dt_orig))::int AS hr,
         COUNT(*) AS n,
         -- I-1: same no-answer guard as calls.duration (0 seconds, never the
         -- raw disconnect epoch, for legs with a disconnect but no connect).
         -- NOTE: cdr stores dt_connect=0 (not NULL) for no-answer legs —
         -- calls.py nullifies zeros via NULLIF before its CASE; here the
         -- guard must test the zero too.
         SUM(CASE WHEN COALESCE(c.dt_connect, 0) <> 0
                  THEN GREATEST(COALESCE(c.dt_disc, 0) - c.dt_connect, 0)
                  ELSE 0 END) AS total
  FROM cdr c
  JOIN _pairs p ON c.cluster_id = p.cluster_id
               AND (to_timestamp(c.dt_orig) AT TIME ZONE 'UTC')::date = p.day
  CROSS JOIN LATERAL (VALUES (c.orig_device), (c.dest_device)) x(device_name)
  WHERE x.device_name IS NOT NULL
  GROUP BY 1, 2, 3, 4
)
SELECT s.cluster_id, s.device_name, s.day,
       COALESCE(dr.role, 'other'),
       SUM(s.n), SUM(s.total),
       (ARRAY_AGG(s.hr ORDER BY s.n DESC, s.hr))[1],
       MAX(s.n)
FROM sides s
LEFT JOIN device_roles dr ON dr.device_name = s.device_name
GROUP BY 1, 2, 3, 4
ON CONFLICT (cluster_id, device_name, day) DO UPDATE SET
  role = EXCLUDED.role, call_count = EXCLUDED.call_count,
  total_duration = EXCLUDED.total_duration, peak_hour = EXCLUDED.peak_hour,
  peak_hour_calls = EXCLUDED.peak_hour_calls;
"""


def refresh_device_daily(conn, filename):
    conn.execute("CREATE TEMP TABLE _pairs AS "
                 "SELECT DISTINCT cluster_id, (to_timestamp(dt_orig) AT TIME ZONE 'UTC')::date AS day "
                 "FROM cdr WHERE filename=%s", (filename,))
    conn.execute(DAILY_SQL)
    conn.execute("DROP TABLE _pairs")


def refresh_device_daily_batch(conn):
    """Rebuild device_daily over ALL (cluster, day) pairs — used by the seed."""
    conn.execute("CREATE TEMP TABLE _pairs AS "
                 "SELECT DISTINCT cluster_id, (to_timestamp(dt_orig) AT TIME ZONE 'UTC')::date AS day "
                 "FROM cdr")
    conn.execute(DAILY_SQL)
    conn.execute("DROP TABLE _pairs")

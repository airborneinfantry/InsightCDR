"""calls rebuild: cdr -> one row per call instance.

Grain is (cluster_id, call_id, call_manager_id, orig_call_leg_id, dt_start):
CUCM recycles globalCallID_callId, so a stitch key + leg id recurs over time
for distinct real calls. Each CDR instance is (stitch key, leg, dt_orig); the
row's dt_start is MIN(dt_orig) of that instance, which keeps recycled calls
distinct (without it ~6 real calls collapsed into 1 on the Cluster-02 corpus).
"""

UPSERT_SQL = """
INSERT INTO calls (cluster_id, call_id, call_manager_id, orig_call_leg_id,
  dt_start, dt_connect, dt_disc, calling_party, called_party,
  orig_partition, dest_partition, orig_user_id, orig_device, dest_device,
  duration, disposition, cause_code, mos_best, mos_avg, leg_count)
SELECT cluster_id, call_id, call_manager_id, leg_id,
  dt_start, dt_connect, dt_disc,
  calling_party, called_party, orig_partition, dest_partition,
  orig_user_id, orig_device, dest_device,
  -- I-1: a leg with a disconnect but no connect (no-answer: dateTimeConnect=0)
  -- must contribute 0 seconds, not the raw disconnect epoch; an answered
  -- leg with no disconnect must contribute 0, not NULL (COALESCE matches
  -- the device_daily rollup in daily.py).
  CASE WHEN dt_connect IS NOT NULL
       THEN GREATEST(COALESCE(dt_disc, 0) - dt_connect, 0)
       ELSE 0 END AS duration,
  CASE WHEN dt_connect IS NOT NULL THEN 'answered'
       WHEN cause_code <> 0 THEN 'failed'
       ELSE 'no-answer' END AS disposition,
  cause_code, mos_best, mos_avg, leg_count
FROM (
  SELECT c.cluster_id, c.call_id, c.call_manager_id,
         COALESCE(c.orig_call_leg_id, 0) AS leg_id,
         MIN(c.dt_orig) AS dt_start,
         MIN(NULLIF(c.dt_connect, 0)) AS dt_connect,
         MAX(NULLIF(c.dt_disc, 0)) AS dt_disc,
         (ARRAY_AGG(c.calling_party ORDER BY c.dt_orig))[1] AS calling_party,
         (ARRAY_AGG(c.called_party ORDER BY c.dt_orig))[1] AS called_party,
         (ARRAY_AGG(c.orig_partition ORDER BY c.dt_orig))[1] AS orig_partition,
         (ARRAY_AGG(c.dest_partition ORDER BY c.dt_orig))[1] AS dest_partition,
         MIN(c.orig_user_id) AS orig_user_id,
         (ARRAY_AGG(c.orig_device ORDER BY c.dt_orig))[1] AS orig_device,
         (ARRAY_AGG(c.dest_device ORDER BY c.dt_orig))[1] AS dest_device,
         MAX(COALESCE(c.cause_code, 0)) AS cause_code,
         COUNT(*) AS leg_count
  FROM cdr c
  WHERE (c.cluster_id, c.call_id, c.call_manager_id) IN (
    SELECT cluster_id, call_id, call_manager_id FROM _keys)
  GROUP BY 1, 2, 3, 4, c.dt_orig
) g
LEFT JOIN LATERAL (
  SELECT MAX(m.mos_lqo) AS mos_best, AVG(m.mos_lqo) AS mos_avg
  FROM cmr m
  WHERE m.cluster_id = g.cluster_id
    AND m.call_id = g.call_id
    AND m.call_manager_id = g.call_manager_id
) q ON TRUE
ON CONFLICT (cluster_id, call_id, call_manager_id, orig_call_leg_id, dt_start)
DO UPDATE SET
  dt_start = EXCLUDED.dt_start, dt_connect = EXCLUDED.dt_connect,
  dt_disc = EXCLUDED.dt_disc, calling_party = EXCLUDED.calling_party,
  called_party = EXCLUDED.called_party, orig_partition = EXCLUDED.orig_partition,
  dest_partition = EXCLUDED.dest_partition, orig_user_id = EXCLUDED.orig_user_id,
  orig_device = EXCLUDED.orig_device, dest_device = EXCLUDED.dest_device,
  duration = EXCLUDED.duration, disposition = EXCLUDED.disposition,
  cause_code = EXCLUDED.cause_code, mos_best = EXCLUDED.mos_best,
  mos_avg = EXCLUDED.mos_avg, leg_count = EXCLUDED.leg_count;
"""



def rebuild_calls(conn, keys):
    """keys: list of (cluster_id, call_id, call_manager_id) 3-tuples."""
    if not keys:
        return 0
    conn.execute("DROP TABLE IF EXISTS _keys")
    conn.execute("CREATE TEMP TABLE _keys (cluster_id text, call_id bigint, call_manager_id int)")
    with conn.cursor() as cur:
        with cur.copy("COPY _keys FROM STDIN") as cp:
            for k in keys:
                cp.write_row(k)
    cur = conn.execute(UPSERT_SQL)
    n = cur.rowcount or 0
    conn.execute("DROP TABLE _keys")
    return n

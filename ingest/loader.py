import json, os
from ingest.fieldmap import parse_cucm_file, row_get, row_int
from ingest.vqmetrics import parse_var_vq_metrics
from ingest.calls import rebuild_calls
from ingest.daily import refresh_device_daily


def _g(idx, row, *names):
    """Version-tolerant column getter: first name present in the header, else None."""
    for n in names:
        if n.lower() in idx:
            return row_get(idx, row, n)
    return None


def _extract_cdr(idx, row, filename):
    return (
        _g(idx, row, "globalCallId_ClusterID") or "unknown",
        int(_g(idx, row, "globalCallID_callId")),
        int(_g(idx, row, "globalCallID_callManagerId")),
        int(_g(idx, row, "dateTimeOrigination")),
        row_int(_g(idx, row, "dateTimeConnect")),
        row_int(_g(idx, row, "dateTimeDisconnect")),
        _g(idx, row, "callingPartyNumber"),
        _g(idx, row, "originalCalledPartyNumber"),
        _g(idx, row, "callingPartyNumPartition"),
        _g(idx, row, "finalPartyPartition", "originalCalledPartyPartition"),
        _g(idx, row, "callingPartyUnicodeLoginUserID"),
        _g(idx, row, "lastRedirectDnUnicodeLoginUserId", "lastRedirectDnLoginUserId"),
        _g(idx, row, "origDeviceName"),
        _g(idx, row, "destDeviceName"),
        row_int(_g(idx, row, "duration")),
        row_int(_g(idx, row, "destCause_value")),
        row_int(_g(idx, row, "origLegCallIdentifier")),
        json.dumps({h: row[i] for h, i in idx.items()}),
        filename,
    )


def _extract_cmr(idx, row, filename):
    vq = parse_var_vq_metrics(_g(idx, row, "varVQMetrics") or "")
    return (
        _g(idx, row, "globalCallId_ClusterID") or "unknown",
        int(_g(idx, row, "globalCallId_callId")),
        int(_g(idx, row, "globalCallId_callManagerId")),
        _g(idx, row, "deviceName"),
        row_int(_g(idx, row, "dateTimeStamp")),
        row_int(_g(idx, row, "numberPacketsSent")),
        row_int(_g(idx, row, "numberPacketsReceived")),
        row_int(_g(idx, row, "numberPacketsLost")),
        row_int(_g(idx, row, "numberOctetsSent")),
        row_int(_g(idx, row, "numberOctetsReceived")),
        row_int(_g(idx, row, "varJitter", "averageJitter")),
        row_int(_g(idx, row, "varLatency", "averageLatency")),
        vq.get("mos_lqo"), vq.get("mos_cqo"),
        json.dumps({h: row[i] for h, i in idx.items()}),
        filename,
    )


CDR_COLS = ("cluster_id, call_id, call_manager_id, dt_orig, dt_connect, dt_disc, calling_party, "
            "called_party, orig_partition, dest_partition, orig_user_id, dest_user_id, orig_device, "
            "dest_device, duration, cause_code, orig_call_leg_id, raw, filename")
CMR_COLS = ("cluster_id, call_id, call_manager_id, device_name, dt, packets_sent, packets_recv, "
            "packets_lost, octets_sent, octets_recv, jitter, latency, mos_lqo, mos_cqo, raw, filename")


def _copy(conn, table_cols, recs):
    with conn.cursor() as cur:
        with cur.copy(f"COPY {table_cols} FROM STDIN") as cp:
            for r in recs:
                cp.write_row(r)


def _quarantine(conn, path, kind, err):
    base = os.path.basename(path)
    dest = os.path.join(os.path.dirname(path), "..", "quarantine", base)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    os.replace(path, dest)
    # Upsert: a second error for the same filename must not hit the
    # UNIQUE(filename) constraint with a bare INSERT (M-4b).
    conn.execute("INSERT INTO ingest_log (filename, kind, bytes, rows_loaded, status, error) "
                 "VALUES (%s,%s,%s,0,'error',%s) "
                 "ON CONFLICT (filename) DO UPDATE SET kind = EXCLUDED.kind, "
                 "bytes = EXCLUDED.bytes, rows_loaded = 0, status = 'error', "
                 "error = EXCLUDED.error, loaded_at = now()",
                 (base, kind, os.path.getsize(dest), err))


def _rebuild_keys_for(conn, table, base):
    return conn.execute(
        f"SELECT DISTINCT cluster_id, call_id, call_manager_id FROM {table} WHERE filename=%s",
        (base,)).fetchall()


def load_file(conn, path: str):
    base = os.path.basename(path)
    prev = conn.execute("SELECT status FROM ingest_log WHERE filename=%s", (base,)).fetchone()
    if prev and prev[0] != "error":
        return None, 0, "duplicate"
    if prev:
        # A prior 'error' row is a retry marker, not a duplicate (M-4a):
        # drop the stale row so this attempt can record its own outcome.
        conn.execute("DELETE FROM ingest_log WHERE filename=%s", (base,))
    u = base.upper()
    kind = "cdr" if u.endswith("CDR.TXT") else ("cmr" if u.endswith("CMR.TXT") else None)
    if kind is None:
        _quarantine(conn, path, "unknown", "unrecognized file kind (no _CDR/_CMR marker)")
        return None, 0, "error"
    try:
        idx, rows = parse_cucm_file(path)
        recs = []
        for row in rows:
            recs.append(_extract_cdr(idx, row, base) if kind == "cdr" else _extract_cmr(idx, row, base))
        cols = CDR_COLS if kind == "cdr" else CMR_COLS
        _copy(conn, f"cdr({cols})" if kind == "cdr" else f"cmr({cols})", recs)
    except Exception as e:
        _quarantine(conn, path, kind, str(e))
        return kind, 0, "error"
    conn.execute("INSERT INTO ingest_log (filename, kind, bytes, rows_loaded, status) "
                 "VALUES (%s,%s,%s,%s,'loaded')", (base, kind, os.path.getsize(path), len(recs)))
    os.remove(path)  # processed out of the spool
    if os.environ.get("IC_SEED_FAST"):
        return kind, len(recs), "loaded"
    keys = set(_rebuild_keys_for(conn, "cdr", base))
    if kind == "cmr":
        keys |= set(_rebuild_keys_for(conn, "cmr", base))
    for k in sorted(keys):
        rebuild_calls(conn, [k])
    refresh_device_daily(conn, base)
    return kind, len(recs), "loaded"

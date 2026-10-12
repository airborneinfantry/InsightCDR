"""Quality diagnostics (spec §4): worst-calls explorer + per-device quality history.

`/quality/worst` ranks calls by lowest mos_best and shows per-call CMR metrics
(packet loss / jitter / latency). Call filters reuse Task 8's build_where plus
its route-level _normalize helper (dates/numbers must bind as the right types);
per-call CMR metrics are fetched in a second key-set query so the candidate
page stays bounded and index-driven.

`/quality/device/{name}` aggregates cmr per UTC day for one device — the point
is to answer "is this device degraded, or was it one bad call": a sustained low
avg-MOS trend means the device; a single outlier day among normal neighbours
means one bad call.

The sanitized corpus carries no CMR quality metrics (extracted metric columns
are NULL throughout), so on production data both pages legitimately render
their empty states.
"""
import os
import urllib.parse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from app.db import get_conn
from app.config import load_config
from app.trend import svg_spark
from app.routes.search import build_where, _normalize, _fmt_ts, _fmt_dur, _clusters

router = APIRouter()

ROLES = ["admin", "analyst"]  # quality diagnostics; viewers use dashboard/search
WORST_LIMIT = 50

CANDIDATE_SQL = (
    "SELECT * FROM ("
    "  SELECT DISTINCT ON (cluster_id, call_id, call_manager_id)"
    "         cluster_id, call_id, call_manager_id, dt_start, calling_party, called_party,"
    "         orig_device, dest_device, disposition, duration, mos_best"
    "    FROM calls WHERE ({where}) AND mos_best IS NOT NULL"
    "   ORDER BY cluster_id, call_id, call_manager_id, mos_best ASC,"
    "            orig_call_leg_id, dt_start"
    ") s ORDER BY mos_best ASC LIMIT %s"
)

CANDIDATE_METRICS_SQL = (
    "SELECT m.cluster_id, m.call_id, m.call_manager_id,"
    "       SUM(m.packets_lost) AS lost, MAX(m.jitter) AS jitter,"
    "       MAX(m.latency) AS latency, COUNT(*) AS legs"
    "  FROM cmr m"
    "  JOIN (VALUES {values}) AS k(cluster_id, call_id, call_manager_id)"
    "    ON m.cluster_id = k.cluster_id::text"
    "   AND m.call_id = k.call_id::bigint"
    "   AND m.call_manager_id = k.call_manager_id::int"
    " GROUP BY 1, 2, 3"
)

DEVICE_HISTORY_SQL = (
    "SELECT (to_timestamp(dt) AT TIME ZONE 'UTC')::date AS day,"
    "       COUNT(*) AS legs,"
    "       AVG(mos_lqo) AS avg_mos,"
    "       SUM(packets_lost) AS lost,"
    "       AVG(jitter) AS jitter,"
    "       AVG(latency) AS latency"
    "  FROM cmr WHERE device_name=%s"
    " GROUP BY 1 ORDER BY 1 DESC"
)

DEVICE_CALLS_SQL = (
    "SELECT COUNT(*) FILTER (WHERE orig_device = %s),"
    "       COUNT(*) FILTER (WHERE dest_device = %s)"
    "  FROM calls WHERE orig_device = %s OR dest_device = %s"
)


def _auth(request: Request):
    # require() lives in app.main; import lazily to avoid a circular import.
    from app.main import require
    return require(ROLES)(request)


def _f(x, nd=1):
    return "—" if x is None else f"{float(x):.{nd}f}"


def _num(x):
    return "—" if x is None else f"{int(x):,}"


def _candidate_metrics(conn, keys):
    """Aggregate cmr metrics for the candidate stitch keys (VALUES join)."""
    if not keys:
        return {}
    vh = ", ".join(["(%s, %s, %s)"] * len(keys))
    params = [v for key in keys for v in key]
    agg = conn.execute(CANDIDATE_METRICS_SQL.format(values=vh), params).fetchall()
    return {(r[0], r[1], r[2]): (r[3], r[4], r[5], r[6]) for r in agg}


@router.get("/quality")
def quality_home():
    # base.html nav links /quality; land the user on the worst-calls explorer.
    return RedirectResponse("/quality/worst", status_code=303)


@router.get("/quality/worst")
def worst(request: Request, auth: tuple = Depends(_auth), device: str | None = None,
          cluster: str | None = None, from_ts: str | None = None,
          to_ts: str | None = None, mos_max: str | None = None):
    request, username = auth
    from app.main import templates
    f = {k: v for k, v in {"device": device, "cluster": cluster, "from_ts": from_ts,
                           "to_ts": to_ts, "mos_max": mos_max}.items() if v}
    where, params = build_where(_normalize(f))
    conn = get_conn(load_config(dict(os.environ)))
    try:
        cand = conn.execute(CANDIDATE_SQL.format(where=where),
                            params + [WORST_LIMIT]).fetchall()
        metrics = _candidate_metrics(conn, [(r[0], r[1], r[2]) for r in cand])
        clusters = _clusters(conn)
    finally:
        conn.close()
    rows = []
    for r in cand:
        m = metrics.get((r[0], r[1], r[2]), (None, None, None, 0))
        rows.append({
            "link": f"/call/{r[0]}/{r[1]}/{r[2]}",
            "dt": _fmt_ts(r[3]),
            "calling": r[4] or "—", "called": r[5] or "—",
            "odev": r[6] or "—", "ddev": r[7] or "—",
            "odev_link": f"/quality/device/{urllib.parse.quote(r[6], safe='')}" if r[6] else None,
            "ddev_link": f"/quality/device/{urllib.parse.quote(r[7], safe='')}" if r[7] else None,
            "disp": r[8], "dur": _fmt_dur(r[9]), "mos": _f(r[10], 2),
            "lost": _num(m[0]), "jitter": _f(m[1], 1), "latency": _f(m[2], 1),
        })
    return templates.TemplateResponse(request, "worst.html", {
        "username": username, "rows": rows, "f": f, "clusters": clusters,
    })


@router.get("/quality/device/{name}")
def device_history(name: str, request: Request, auth: tuple = Depends(_auth)):
    request, username = auth
    from app.main import templates
    conn = get_conn(load_config(dict(os.environ)))
    try:
        hist = conn.execute(DEVICE_HISTORY_SQL, (name,)).fetchall()
        n_orig, n_dest = conn.execute(DEVICE_CALLS_SQL, (name, name, name, name)).fetchone()
    finally:
        conn.close()
    chrono = list(reversed(hist))  # oldest -> newest for the trend line
    mos_vals = [r[2] for r in chrono]
    spark = None
    if mos_vals and any(v is not None for v in mos_vals):
        spark = svg_spark(mos_vals, labels=[r[0].strftime("%m-%d") for r in chrono])
    days = [{
        "day": r[0].strftime("%Y-%m-%d"),
        "legs": r[1],
        "avg_mos": _f(r[2], 2), "lost": _num(r[3]),
        "jitter": _f(r[4], 1), "latency": _f(r[5], 1),
    } for r in hist]
    return templates.TemplateResponse(request, "device.html", {
        "username": username, "device": name, "days": days, "spark": spark,
        "total_legs": sum(r[1] for r in hist), "n_days": len(hist),
        "n_orig": n_orig, "n_dest": n_dest,
        "search_link": "/search/results?device=" + urllib.parse.quote(name),
    })

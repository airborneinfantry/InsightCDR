"""Call search + stitched call-detail page (spec §4).

`build_where` over `calls` is the shared WHERE builder reused by reports (Task 11).
The detail page stitches cdr/cmr by (cluster_id, call_id, call_manager_id); recycled
call_ids across time render as sibling call rows (each a distinct instance keyed by
dt_start) rather than one merged call.
"""
import datetime as dt
import os
import re
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Request
from app.db import get_conn
from app.config import load_config

router = APIRouter()

ROLES = ["admin", "analyst", "viewer"]
PAGE_SIZE = 50

CLAUSES = {
    "number": ("(calling_party = %s OR called_party = %s)",),
    "user": ("orig_user_id = %s",),
    "device": ("(orig_device = %s OR dest_device = %s)",),
    "cluster": ("cluster_id = %s",),
    "from_ts": ("dt_start >= %s",),
    "to_ts": ("dt_start <= %s",),
    "min_duration": ("duration >= %s",),
    "disposition": ("disposition = %s",),
    "mos_max": ("mos_best <= %s",),
}


def build_where(f: dict):
    clauses, params = [], []
    for key, (clause,) in CLAUSES.items():
        v = (f.get(key) or "").strip()
        if not v:
            continue
        clauses.append(clause)
        if key in ("number", "device"):
            params.extend([v, v])
        else:
            params.append(v)
    where = " AND ".join(clauses) if clauses else "TRUE"
    return where, params


def _parse_ts(v: str) -> float:
    """Accept datetime-local strings ('2026-08-01T12:30'), ISO dates, or epoch seconds."""
    t = str(v).strip()
    try:
        d = dt.datetime.fromisoformat(t)
    except ValueError:
        try:
            return float(t)
        except ValueError:
            raise HTTPException(status_code=400,
                                 detail=f"Invalid datetime filter: {t!r}")
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.timestamp()


def _normalize(f: dict) -> dict:
    """Coerce filter values into strings the parametrized clauses can bind.

    dt_start is bigint epoch, duration int, mos_best real — plain text params
    would fail the bigint>=text comparison, so numeric/datetime filters are
    converted here (build_where stays verbatim and string-only).
    """
    f = dict(f)
    for k in ("from_ts", "to_ts"):
        if f.get(k):
            ts = _parse_ts(f[k])
            # M-6: a date-only `to` is advanced to 23:59:59 so the end day is
            # inclusive (matching the reports' inclusive-date behavior). Epoch
            # passthrough and datetime-local inputs stay exact.
            if k == "to_ts" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(f[k]).strip()):
                ts += 86399
            f[k] = str(int(ts))
    if f.get("min_duration"):
        try:
            f["min_duration"] = str(int(float(f["min_duration"])))
        except ValueError:
            raise HTTPException(status_code=400,
                                 detail="Min duration must be a number of seconds")
    if f.get("mos_max"):
        try:
            f["mos_max"] = str(float(f["mos_max"]))
        except ValueError:
            raise HTTPException(status_code=400, detail="Max MOS must be a number")
    return f


def _fmt_ts(ts):
    if not ts:
        return "—"
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _fmt_dur(s):
    if s is None:
        return "—"
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _auth(request: Request):
    # require() lives in app.main; import lazily to avoid a circular import.
    from app.main import require
    return require(ROLES)(request)


def _clusters(conn):
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT cluster_id FROM calls ORDER BY 1").fetchall()]


CDR_LEG_SQL = (
    "SELECT id, dt_orig, dt_connect, dt_disc, calling_party, called_party, "
    "orig_device, dest_device, orig_user_id, dest_user_id, duration, cause_code, "
    "orig_call_leg_id, filename "
    "FROM cdr WHERE cluster_id=%s AND call_id=%s AND call_manager_id=%s "
    "ORDER BY dt_orig, id"
)

CMR_LEG_SQL = (
    "SELECT device_name, dt, packets_sent, packets_recv, packets_lost, "
    "jitter, latency, mos_lqo, mos_cqo, filename "
    "FROM cmr WHERE cluster_id=%s AND call_id=%s AND call_manager_id=%s "
    "ORDER BY dt, id"
)

CALLS_SQL = (
    "SELECT cluster_id, call_id, call_manager_id, orig_call_leg_id, dt_start, dt_connect, "
    "dt_disc, calling_party, called_party, orig_partition, dest_partition, orig_user_id, "
    "orig_device, dest_device, duration, disposition, cause_code, mos_best, mos_avg, leg_count "
    "FROM calls WHERE cluster_id=%s AND call_id=%s AND call_manager_id=%s "
    "ORDER BY dt_start"
)

_KIND_RANK = {"originate": 0, "connect": 1, "disconnect": 2}


@router.get("/search")
def search_page(auth: tuple = Depends(_auth)):
    request, username = auth
    from app.main import templates
    conn = get_conn(load_config(dict(os.environ)))
    try:
        clusters = _clusters(conn)
    finally:
        conn.close()
    return templates.TemplateResponse(request, "search.html", {
        "username": username, "clusters": clusters, "f": {}, "results": None,
    })


@router.get("/search/results")
def search_results(
    auth: tuple = Depends(_auth),
    number: str | None = None, user: str | None = None, device: str | None = None,
    cluster: str | None = None, from_ts: str | None = None, to_ts: str | None = None,
    min_duration: str | None = None, disposition: str | None = None,
    mos_max: str | None = None, page: int = 1,
):
    request, username = auth
    from app.main import templates
    f = {k: v for k, v in {"number": number, "user": user, "device": device,
                           "cluster": cluster, "from_ts": from_ts, "to_ts": to_ts,
                           "min_duration": min_duration, "disposition": disposition,
                           "mos_max": mos_max}.items() if v}
    f = _normalize(f)
    page = max(1, page)
    offset = (page - 1) * PAGE_SIZE
    where, params = build_where(f)
    conn = get_conn(load_config(dict(os.environ)))
    try:
        rows = conn.execute(
            f"SELECT cluster_id, call_id, call_manager_id, dt_start, calling_party, called_party, "
            f"orig_device, dest_device, disposition, duration, mos_best "
            f"FROM calls WHERE {where} ORDER BY dt_start DESC LIMIT 50 OFFSET %s",
            params + [offset]).fetchall()
        total = conn.execute(f"SELECT count(*) FROM calls WHERE {where}",
                             params).fetchone()[0]
        # per-call CMR quality aggregates for THIS page's calls only (keyset
        # query, bounded by the 50 stitch keys just fetched — never a full scan)
        metrics = {}
        if rows:
            values = ", ".join("(%s, %s, %s)" for _ in rows)
            kparams = [v for r in rows for v in (r[0], r[1], r[2])]
            mrows = conn.execute(
                "SELECT m.cluster_id, m.call_id, m.call_manager_id,"
                "       SUM(m.packets_lost) AS lost, MAX(m.jitter) AS jitter,"
                "       MAX(m.latency) AS latency"
                "  FROM cmr m"
                f"  JOIN (VALUES {values}) AS k(cluster_id, call_id, call_manager_id)"
                "    ON m.cluster_id = k.cluster_id::text"
                "   AND m.call_id = k.call_id::bigint"
                "   AND m.call_manager_id = k.call_manager_id::int"
                " GROUP BY 1, 2, 3", kparams).fetchall()
            metrics = {(m[0], m[1], m[2]): m[3:] for m in mrows}
        clusters = _clusters(conn)
    finally:
        conn.close()
    def _m(key, i):
        v = metrics.get(key, (None,) * 3)[i]
        return v if v is not None else "—"

    out = [{
        "link": f"/call/{r[0]}/{r[1]}/{r[2]}",
        "dt": _fmt_ts(r[3]),
        "calling": r[4] or "—", "called": r[5] or "—",
        "odev": r[6] or "—", "ddev": r[7] or "—",
        "disp": r[8],
        "dur": _fmt_dur(r[9]),
        "mos": f"{r[10]:.2f}" if r[10] is not None else "—",
        "lost": _m((r[0], r[1], r[2]), 0),
        "jitter": _m((r[0], r[1], r[2]), 1),
        "latency": _m((r[0], r[1], r[2]), 2),
    } for r in rows]
    pages = max(1, -(-total // PAGE_SIZE))
    qs = urllib.parse.urlencode({k: v for k, v in f.items() if k != "page"})
    # The form re-renders with datetime-local inputs: epoch strings from
    # _normalize would be dropped as invalid values, so convert back to
    # "YYYY-MM-DDTHH:MM" (UTC, matching _parse_ts's naive-input treatment).
    f_disp = dict(f)
    for k in ("from_ts", "to_ts"):
        v = f_disp.get(k)
        if v:
            f_disp[k] = dt.datetime.fromtimestamp(int(v), tz=dt.timezone.utc)\
                .strftime("%Y-%m-%dT%H:%M")
    ctx = {"username": username, "rows": out, "total": total, "page": page,
           "pages": pages, "qs": qs, "clusters": clusters, "f": f_disp, "results": True}
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "results.html", ctx)
    return templates.TemplateResponse(request, "search.html", ctx)


@router.get("/call/{cluster_id}/{call_id}/{cm_id}")
def call_detail(cluster_id: str, call_id: int, cm_id: int, auth: tuple = Depends(_auth)):
    request, username = auth
    from app.main import templates
    conn = get_conn(load_config(dict(os.environ)))
    try:
        calls_rows = conn.execute(CALLS_SQL, (cluster_id, call_id, cm_id)).fetchall()
        cdr_legs = conn.execute(CDR_LEG_SQL, (cluster_id, call_id, cm_id)).fetchall()
        cmr_legs = conn.execute(CMR_LEG_SQL, (cluster_id, call_id, cm_id)).fetchall()
    finally:
        conn.close()
    if not calls_rows and not cdr_legs:
        raise HTTPException(status_code=404, detail="Call not found")
    # Python-assembled timeline: originate -> connect -> disconnect per CDR leg,
    # transfer/conference legs appearing as separate rows sharing the stitch key.
    events = []
    for leg in cdr_legs:
        leg_id = leg[12]
        events.append({"ts": leg[1], "kind": "originate", "leg": leg_id, "cause": None})
        if leg[2]:
            events.append({"ts": leg[2], "kind": "connect", "leg": leg_id, "cause": None})
        if leg[3]:
            events.append({"ts": leg[3], "kind": "disconnect", "leg": leg_id,
                           "cause": leg[11]})
    events.sort(key=lambda e: (e["ts"], _KIND_RANK[e["kind"]]))
    timeline = [{"dt": _fmt_ts(e["ts"]), "kind": e["kind"], "leg": e["leg"],
                 "cause": e["cause"]} for e in events]
    s = calls_rows[0] if calls_rows else None
    summary = None
    if s:
        summary = {
            "leg_id": s[3],
            "dt_start": _fmt_ts(s[4]), "calling": s[7] or "—", "called": s[8] or "—",
            "user": s[11] or "—",
            "odev": s[12] or "—", "ddev": s[13] or "—",
            "dur": _fmt_dur(s[14]), "disp": s[15],
            "mos_best": f"{s[17]:.2f}" if s[17] is not None else "—",
            "mos_avg": f"{s[18]:.2f}" if s[18] is not None else "—",
        }
    extras = [{
        "leg_id": c[3], "dt_start": _fmt_ts(c[4]), "dur": _fmt_dur(c[14]),
        "disp": c[15], "mos": f"{c[17]:.2f}" if c[17] is not None else "—",
    } for c in calls_rows[1:]]
    cdr_out = [{
        "dt_orig": _fmt_ts(leg[1]), "dt_connect": _fmt_ts(leg[2]),
        "dt_disc": _fmt_ts(leg[3]),
        "calling": leg[4] or "—", "called": leg[5] or "—",
        "odev": leg[6] or "—", "ddev": leg[7] or "—",
        "user": leg[8] or "—",
        "dur": _fmt_dur(leg[10]), "cause": leg[11],
        "leg": leg[12], "filename": leg[13],
    } for leg in cdr_legs]
    cmr_out = [{
        "device": m[0] or "—", "dt": _fmt_ts(m[1]),
        "sent": m[2] if m[2] is not None else "—",
        "recv": m[3] if m[3] is not None else "—",
        "lost": m[4] if m[4] is not None else "—",
        "jitter": m[7] if m[7] is not None else "—",
        "latency": m[8] if m[8] is not None else "—",
        "mos_lqo": f"{m[9]:.2f}" if m[9] is not None else "—",
        "mos_cqo": f"{m[10]:.2f}" if m[10] is not None else "—",
    } for m in cmr_legs]
    ctx = {
        "username": username,
        "cluster_id": cluster_id, "call_id": call_id, "cm_id": cm_id,
        "summary": summary, "extras": extras,
        "timeline": timeline, "cdr_legs": cdr_out, "cmr_legs": cmr_out,
    }
    return templates.TemplateResponse(request, "call.html", ctx)

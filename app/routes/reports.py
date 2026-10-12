"""In-app printable reports (Task 11): daily call summary, quality, utilization.

Generate-on-demand HTML - print-ready (the template print stylesheet hides the
topbar/banner and forces a white background), no email. All three sit behind
require(["admin", "analyst"]).

The quality report reuses Task 8's search._normalize + build_where so its
from/to dates bind as bigint epoch against dt_start (a date-only `to` is
advanced to 23:59:59 so the end day is inclusive). The utilization report
filters device_daily.day with real date params (day is a date, not an epoch).
"""
import datetime as dt
import os
import urllib.parse

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from app.db import get_conn
from app.config import load_config
from app.routes.search import build_where, _normalize
from app.routes.utilization import _date as _parse_date

router = APIRouter()

ROLES = ["admin", "analyst"]  # printable reports; viewers use dashboard/search

SUMMARY_SQL = (
    "SELECT EXTRACT(hour FROM to_timestamp(dt_start) AT TIME ZONE 'UTC')::int AS hr, "
    "COUNT(*), SUM((disposition='answered')::int), AVG(mos_best) "
    "FROM calls WHERE {where} GROUP BY 1 ORDER BY 1"
)
SUMMARY_TOTALS_SQL = (
    "SELECT COUNT(*), SUM((disposition='answered')::int), "
    "SUM((disposition='failed')::int), AVG(mos_best) "
    "FROM calls WHERE {where}"
)
QUALITY_SQL = (
    "SELECT orig_device, COUNT(*), AVG(mos_best), SUM((mos_best<3.5)::int), "
    "SUM((mos_best IS NOT NULL)::int) "
    "FROM calls WHERE {where} GROUP BY 1 ORDER BY 4 DESC NULLS LAST, 2 DESC"
)
UTIL_SQL = (
    "SELECT cluster_id, device_name, SUM(call_count), SUM(total_duration)/60, "
    "MAX(peak_hour), MAX(peak_hour_calls), COUNT(*) "
    "FROM device_daily WHERE (%s::date IS NULL OR day >= %s) "
    "AND (%s::date IS NULL OR day <= %s) "
    "GROUP BY 1, 2 ORDER BY 3 DESC"
)
CLUSTERS_SQL = "SELECT DISTINCT cluster_id FROM calls ORDER BY 1"

TODAY_FMT = "%Y-%m-%d"


def _auth(request: Request):
    # require() lives in app.main; import lazily to avoid a circular import.
    from app.main import require
    return require(ROLES)(request)


def _f(x, nd=2):
    return "-" if x is None else f"{float(x):.{nd}f}"


def _num(x):
    return "-" if x is None else f"{int(x):,}"


def _pct(part, total):
    if not total:
        return "-"
    return f"{100.0 * float(part) / float(total):.1f}%"


def _now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _today():
    return dt.datetime.now(dt.timezone.utc).strftime(TODAY_FMT)


@router.get("/reports")
def reports_index():
    # base.html nav links /reports; land on the daily call-summary report.
    return RedirectResponse("/reports/call-summary", status_code=303)


@router.get("/reports/call-summary")
def call_summary(auth: tuple = Depends(_auth), day: str | None = None,
                 cluster: str | None = None):
    request, username = auth
    from app.main import templates
    # Task 8's _normalize parses the day and returns 400 on bad input; the
    # resulting epoch strings bind as bigint against dt_start.
    nf = _normalize({"from_ts": day or _today(), "to_ts": day or _today()})
    from_ts = int(nf["from_ts"])
    to_ts = from_ts + 86400
    clauses = ["dt_start >= %s", "dt_start < %s"]
    params = [from_ts, to_ts]
    if cluster:
        clauses.append("cluster_id = %s")
        params.append(cluster)
    where = " AND ".join(clauses)
    conn = get_conn(load_config(dict(os.environ)))
    try:
        rows = conn.execute(SUMMARY_SQL.format(where=where), params).fetchall()
        totals = conn.execute(SUMMARY_TOTALS_SQL.format(where=where), params).fetchone()
        clusters = [r[0] for r in conn.execute(CLUSTERS_SQL).fetchall()]
    finally:
        conn.close()
    hours = [{
        "hr": f"{r[0]:02d}:00-{r[0]:02d}:59",
        "calls": _num(r[1]),
        "answered": _num(r[2]),
        "pct": _pct(r[2], r[1]),
        "mos": _f(r[3]),
    } for r in rows]
    t_calls, t_answered, t_failed = totals[0], totals[1] or 0, totals[2] or 0
    ctx = {
        "username": username,
        "day": day or _today(),
        "cluster": cluster or "",
        "clusters": clusters,
        "hours": hours,
        "t_calls": _num(t_calls),
        "t_answered": _num(t_answered),
        "t_failed": _num(t_failed),
        "t_pct": _pct(t_answered, t_calls),
        "t_mos": _f(totals[3]),
        "generated": _now(),
    }
    return templates.TemplateResponse(request, "report_call_summary.html", ctx)


@router.get("/reports/quality")
def quality_report(auth: tuple = Depends(_auth), frm: str | None = Query(None, alias="from"),
                   to: str | None = Query(None, alias="to"),
                   cluster: str | None = None):
    request, username = auth
    from app.main import templates
    # Reuse Task 8's _normalize + build_where: dates -> bigint epoch strings,
    # 400 on unparseable input, cluster clause handled by build_where itself.
    # (M-6: _normalize itself advances a date-only `to` by 86399 — end day
    # inclusive — so the report no longer has to re-apply that adjustment.)
    f = {k: v for k, v in {"from_ts": frm, "to_ts": to}.items() if v}
    nf = _normalize(f)
    if cluster:
        nf["cluster"] = cluster
    where, params = build_where(nf)
    conn = get_conn(load_config(dict(os.environ)))
    try:
        rows = conn.execute(QUALITY_SQL.format(where=where), params).fetchall()
        clusters = [r[0] for r in conn.execute(CLUSTERS_SQL).fetchall()]
    finally:
        conn.close()
    devices = [{
        "name": r[0] or "unknown",
        "calls": _num(r[1]),
        "mos": _f(r[2]),
        "below": _num(r[3] or 0),
        "pct": _pct(r[3], r[4]),
        "link": "/quality/device/" + urllib.parse.quote(r[0], safe="") if r[0] else None,
    } for r in rows]
    ctx = {
        "username": username,
        "frm": frm or "",
        "to": to or "",
        "cluster": cluster or "",
        "clusters": clusters,
        "devices": devices,
        "n_devices": len(devices),
        "generated": _now(),
    }
    return templates.TemplateResponse(request, "report_quality.html", ctx)


@router.get("/reports/utilization")
def utilization_report(auth: tuple = Depends(_auth),
                       frm: str | None = Query(None, alias="from"),
                       to: str | None = Query(None, alias="to")):
    request, username = auth
    from app.main import templates
    # device_daily.day is a date column, so the range binds as dates (Task 10's
    # _date helper: 400 on unparseable), not as epochs.
    d1 = _parse_date(frm, "from") if frm else None
    d2 = _parse_date(to, "to") if to else None
    conn = get_conn(load_config(dict(os.environ)))
    try:
        rows = conn.execute(UTIL_SQL, (d1, d1, d2, d2)).fetchall()
    finally:
        conn.close()
    devices = [{
        "name": r[1],
        "cluster": r[0],
        "calls": _num(r[2]),
        "calls_raw": r[2],
        "minutes": _num(r[3]),
        "minutes_raw": int(r[3]) if r[3] is not None else None,
        "peak": "-" if r[4] is None else f"{int(r[4]):02d}:00-{int(r[4]):02d}:59 UTC",
        "peak_calls": _num(r[5]),
        "peak_calls_raw": r[5],
        "days": _num(r[6]),
        "days_raw": r[6],
        "link": "/utilization/device/" + urllib.parse.quote(r[1], safe=""),
    } for r in rows]
    total_calls = sum(r[2] for r in rows)
    total_minutes = sum((r[3] for r in rows if r[3] is not None), 0)
    ctx = {
        "username": username,
        "frm": frm or "",
        "to": to or "",
        "devices": devices,
        "n_devices": len(devices),
        "total_calls": _num(total_calls),
        "total_minutes": _num(int(total_minutes)) if rows else "-",
        "generated": _now(),
    }
    return templates.TemplateResponse(request, "report_utilization.html", ctx)

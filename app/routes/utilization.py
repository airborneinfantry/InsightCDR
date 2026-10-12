"""Utilization dashboard + device-role mapping CRUD (spec §6).

`/utilization` aggregates device_daily over an optional date range per device
(SUM(call_count), SUM(total_duration), MAX(peak_hour_calls), the device's
current role) with a role filter, renders a sortable table and one
calls-per-day SVG trend per role (svg_spark). On the first render after the
seed, device_roles is empty; seed_device_roles() fills pattern-based defaults
(SEP% -> phone etc.) one time, guarded by a count(*) so later renders never
re-scan the calls table.

`/utilization/device/{name}` shows the per-day table (calls / minutes /
peak hour / peak-hour calls) plus a calls-per-day trend for one device.

`GET/POST /utilization/roles` lists and upserts the device -> role mapping;
updated_by records the acting user. device_daily.role is written at rollup
time from the then-current mapping, so the pages prefer the live device_roles
mapping and fall back to the device_daily role column.
"""
import datetime as dt
import os
import urllib.parse

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from app.db import get_conn
from app.config import load_config
from app.trend import svg_spark

router = APIRouter()

ROLES = ["admin", "analyst"]  # utilization + roles CRUD; viewers use dashboard/search
ROLES_ALLOWED = ("trunk", "gateway", "cube", "phone", "other")  # device_roles CHECK

_BASE = (
    "SELECT s.cluster_id, s.device_name, s.day, s.call_count, s.total_duration, "
    "       s.peak_hour_calls, COALESCE(dr.role, s.role) AS role "
    "  FROM device_daily s "
    "  LEFT JOIN device_roles dr ON dr.device_name = s.device_name "
    " WHERE (%s::date IS NULL OR s.day >= %s) "
    "   AND (%s::date IS NULL OR s.day <= %s)"
)

DEVICES_SQL = (
    "SELECT cluster_id, device_name, MIN(role), SUM(call_count), "
    "       SUM(total_duration), MAX(peak_hour_calls), COUNT(*) "
    "  FROM (" + _BASE + ") s "
    " WHERE (%s::text IS NULL OR role = %s) "
    " GROUP BY cluster_id, device_name "
    " ORDER BY 4 DESC LIMIT 500"
)

TREND_SQL = (
    "SELECT cluster_id, role, day, SUM(call_count) "
    "  FROM (" + _BASE + ") s "
    " WHERE (%s::text IS NULL OR role = %s) "
    " GROUP BY cluster_id, role, day ORDER BY day"
)

DEVICE_SQL = (
    "SELECT day, call_count, total_duration, peak_hour, peak_hour_calls "
    "  FROM device_daily WHERE device_name = %s "
    "   AND (%s::date IS NULL OR day >= %s) AND (%s::date IS NULL OR day <= %s) "
    " ORDER BY day"
)

DEVICE_ROLE_SQL = "SELECT role FROM device_roles WHERE device_name = %s"

DEVICE_CALLS_SQL = (
    "SELECT COUNT(*) FILTER (WHERE orig_device = %s), "
    "       COUNT(*) FILTER (WHERE dest_device = %s) "
    "  FROM calls WHERE orig_device = %s OR dest_device = %s"
)

ROLES_LIST_SQL = ("SELECT device_name, role, updated_by, updated_at "
                  "FROM device_roles ORDER BY device_name")

ROLES_UPSERT_SQL = (
    "INSERT INTO device_roles (device_name, role, updated_by) VALUES (%s, %s, %s) "
    "ON CONFLICT (device_name) DO UPDATE SET role = EXCLUDED.role, "
    "updated_by = EXCLUDED.updated_by, updated_at = now()"
)


def seed_device_roles(conn, updated_by="system"):
    n = conn.execute("SELECT count(*) FROM device_roles").fetchone()[0]
    if n:
        return
    conn.execute("""
        INSERT INTO device_roles (device_name, role, updated_by)
        SELECT d, CASE
            WHEN d LIKE 'SEP%%' THEN 'phone'
            WHEN d LIKE '%%CUBE%%' THEN 'cube'
            WHEN d ~ '(?i)(trunk|sip)' THEN 'trunk'
            WHEN d ~ '(?i)(vg|gw|gateway)' THEN 'gateway'
            ELSE 'other' END,
            %s
        FROM (SELECT DISTINCT orig_device AS d FROM calls
              UNION SELECT DISTINCT dest_device FROM calls) x
        WHERE d IS NOT NULL
        ON CONFLICT (device_name) DO NOTHING
    """, (updated_by,))


def _auth(request: Request):
    # require() lives in app.main; import lazily to avoid a circular import.
    from app.main import require
    return require(ROLES)(request)


def _date(v, field):
    try:
        return dt.date.fromisoformat(str(v).strip())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date filter: {field}={v!r}")


def _range(d1, d2):
    """Reject an inverted from > to range (both routes filter device_daily.day)."""
    if d1 and d2 and d1 > d2:
        raise HTTPException(status_code=400, detail="Inverted date range: from > to")
    return d1, d2


def _f(x):
    return "—" if x is None else f"{x:,}"


def _mins(secs):
    # SUM(bigint) arrives as Decimal; coerce before the float division.
    return "—" if secs is None else f"{float(secs) / 60.0:,.0f}"


def _range_params(d1, d2, role):
    # _BASE has 4 date params; each aggregate adds the (role, role) pair.
    return (d1, d1, d2, d2, role, role)


@router.get("/utilization")
def utilization(request: Request, auth: tuple = Depends(_auth),
                dfrom: str | None = Query(None, alias="from"),
                dto: str | None = Query(None, alias="to"),
                role: str | None = None):
    request, username = auth
    from app.main import templates
    d1 = _date(dfrom, "from") if dfrom else None
    d2 = _date(dto, "to") if dto else None
    d1, d2 = _range(d1, d2)
    rolef = role if role in ROLES_ALLOWED else None
    conn = get_conn(load_config(dict(os.environ)))
    try:
        seed_device_roles(conn, updated_by=username)
        rows = conn.execute(DEVICES_SQL, _range_params(d1, d2, rolef)).fetchall()
        trend_rows = conn.execute(TREND_SQL, _range_params(d1, d2, rolef)).fetchall()
    finally:
        conn.close()
    by_key = {}
    for t_cluster, t_role, day, calls in trend_rows:
        by_key.setdefault((t_cluster, t_role), []).append((day, calls))
    trends = []
    for t_cluster, t_role in sorted(by_key):
        pts = by_key[(t_cluster, t_role)]
        trends.append({
            "role": t_role,
            "cluster": t_cluster,
            "svg": svg_spark([v for _, v in pts], labels=[day.strftime("%m-%d") for day, _ in pts]),
            "calls": sum(v for _, v in pts),
            "days": len(pts),
            "link": "/utilization?role=" + urllib.parse.quote(t_role),
        })
    dev_rows = [{
        "cluster": r[0],
        "name": r[1],
        "role": r[2],
        "calls": _f(r[3]), "calls_raw": r[3],
        "minutes": _mins(r[4]), "minutes_raw": r[4],
        "peak": _f(r[5]), "peak_raw": r[5] if r[5] is not None else "",
        "days": _f(r[6]), "days_raw": r[6],
        "link": "/utilization/device/" + urllib.parse.quote(r[1], safe=""),
    } for r in rows]
    ctx = {
        "username": username,
        "rows": dev_rows,
        "trends": trends,
        "n_devices": len(dev_rows),
        "role": role or "",
        "from": dfrom or "",
        "to": dto or "",
        "roles_allowed": ROLES_ALLOWED,
    }
    return templates.TemplateResponse(request, "utilization.html", ctx)


@router.get("/utilization/device/{name}")
def device_utilization(name: str, request: Request, auth: tuple = Depends(_auth),
                       dfrom: str | None = Query(None, alias="from"),
                       dto: str | None = Query(None, alias="to")):
    request, username = auth
    from app.main import templates
    d1 = _date(dfrom, "from") if dfrom else None
    d2 = _date(dto, "to") if dto else None
    d1, d2 = _range(d1, d2)
    conn = get_conn(load_config(dict(os.environ)))
    try:
        hist = conn.execute(DEVICE_SQL, (name, d1, d1, d2, d2)).fetchall()
        cur_role = conn.execute(DEVICE_ROLE_SQL, (name,)).fetchone()
        n_orig, n_dest = conn.execute(DEVICE_CALLS_SQL, (name, name, name, name)).fetchone()
    finally:
        conn.close()
    days = [{
        "day": r[0].strftime("%Y-%m-%d"),
        "calls": _f(r[1]), "calls_raw": r[1],
        "minutes": _mins(r[2]), "minutes_raw": r[2],
        "peak_hour": "—" if r[3] is None else f"{int(r[3]):02d}:00-{int(r[3]):02d}:59 UTC",
        "peak_calls": _f(r[4]), "peak_raw": r[4] if r[4] is not None else "",
    } for r in reversed(hist)]
    spark = None
    if hist:
        spark = svg_spark([r[1] for r in hist], labels=[r[0].strftime("%m-%d") for r in hist])
    total_calls = sum(r[1] for r in hist)
    total_secs = sum(r[2] for r in hist)
    peak = max((r[4] for r in hist if r[4] is not None), default=None)
    return templates.TemplateResponse(request, "utilization.html", {
        "username": username,
        "device": name,
        "cur_role": cur_role[0] if cur_role else None,
        "days": days,
        "spark": spark,
        "total_calls": _f(total_calls),
        "total_minutes": _mins(total_secs) if hist else "—",
        "peak_calls": _f(peak),
        "n_days": len(hist),
        "n_orig": n_orig, "n_dest": n_dest,
        "from": dfrom or "", "to": dto or "",
        "roles_allowed": ROLES_ALLOWED,
    })


@router.get("/utilization/roles")
def roles_page(request: Request, auth: tuple = Depends(_auth)):
    request, username = auth
    from app.main import templates
    conn = get_conn(load_config(dict(os.environ)))
    try:
        mappings = conn.execute(ROLES_LIST_SQL).fetchall()
    finally:
        conn.close()
    rows = [{
        "device": m[0], "role": m[1],
        "updated_by": m[2] or "—",
        "updated_at": m[3].strftime("%Y-%m-%d %H:%M") if m[3] else "—",
    } for m in mappings]
    return templates.TemplateResponse(request, "roles.html", {
        "username": username, "rows": rows, "roles_allowed": ROLES_ALLOWED,
    })


@router.post("/utilization/roles")
def roles_upsert(request: Request, auth: tuple = Depends(_auth),
                 device_name: str = Form(...), role: str = Form(...)):
    request, username = auth
    device = (device_name or "").strip()
    if not device:
        raise HTTPException(status_code=400, detail="device_name is required")
    if role not in ROLES_ALLOWED:
        raise HTTPException(status_code=400, detail=f"Invalid role: {role!r}")
    conn = get_conn(load_config(dict(os.environ)))
    try:
        conn.execute(ROLES_UPSERT_SQL, (device, role, username))
    finally:
        conn.close()
    return RedirectResponse("/utilization/roles", status_code=303)

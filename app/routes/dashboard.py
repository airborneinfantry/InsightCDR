"""GET /dashboard — KPI strip + MOS trend + worst devices, cluster filter."""
import os
import datetime as dt

from fastapi import APIRouter, Depends, Request
from app.db import get_conn
from app.config import load_config
from app.trend import svg_spark

router = APIRouter()

FILTER_SQL = "AND (%s::text IS NULL OR cluster_id = %s)"

KPI_SQL = (
    "SELECT count(*), AVG(mos_best), AVG((mos_best < 3.5)::int), "
    "AVG((disposition='failed')::int) FROM calls "
    "WHERE (%s::text IS NULL OR cluster_id = %s)"
)

WINDOW_SQL = (
    "SELECT count(*) FILTER (WHERE dt_start >= %s), "
    "       count(*) FILTER (WHERE dt_start >= %s), "
    "       count(*) FILTER (WHERE dt_start >= %s) "
    "FROM calls WHERE (%s::text IS NULL OR cluster_id = %s)"
)

TREND_SQL = (
    "SELECT (to_timestamp(dt_start) AT TIME ZONE 'UTC')::date AS day, AVG(mos_best) "
    "FROM calls "
    "WHERE dt_start >= %s " + FILTER_SQL + " "
    "GROUP BY 1 ORDER BY 1"
)

WORST_SQL = (
    "SELECT orig_device, COUNT(*), AVG(mos_best) FROM calls "
    "WHERE mos_best IS NOT NULL " + FILTER_SQL + " "
    "GROUP BY 1 ORDER BY AVG(mos_best) ASC LIMIT 10"
)

CLUSTERS_SQL = "SELECT DISTINCT cluster_id FROM calls ORDER BY 1"

INGEST_ERRORS_SQL = "SELECT count(*) FROM ingest_log WHERE status='error'"


def _pct(x):
    return f"{100.0 * float(x):.1f}%" if x is not None else "—"


def _avg(x):
    return f"{float(x):.2f}" if x is not None else "—"


def _fmt(n):
    return f"{n:,}" if n is not None else "—"


def _auth(request: Request):
    # require() lives in app.main; import lazily to avoid a circular import.
    from app.main import require
    return require(["admin", "analyst", "viewer"])(request)


@router.get("/dashboard")
def dashboard(cluster: str | None = None, auth: tuple = Depends(_auth)):
    request, username = auth
    from app.main import templates
    conn = get_conn(load_config(dict(os.environ)))
    try:
        cl = cluster or None
        p = (cl, cl)
        kpi = conn.execute(KPI_SQL, p).fetchone()
        now = int(dt.datetime.now(dt.timezone.utc).timestamp())
        today = int(dt.datetime.now(dt.timezone.utc)
                    .replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        w = conn.execute(WINDOW_SQL, (today, now - 7 * 86400, now - 30 * 86400, cl, cl)).fetchone()
        trend_rows = conn.execute(TREND_SQL, (now - 14 * 86400, cl, cl)).fetchall()
        svg = svg_spark([r[1] for r in trend_rows],
                        labels=[r[0].strftime("%m-%d") for r in trend_rows])
        worst = conn.execute(WORST_SQL, p).fetchall()
        clusters = conn.execute(CLUSTERS_SQL).fetchall()
        n_ingest_errors = conn.execute(INGEST_ERRORS_SQL).fetchone()[0]
    finally:
        conn.close()
    ctx = {
        "username": username,
        "cluster": cluster,
        "clusters": [c[0] for c in clusters],
        "kpi_calls_total": _fmt(kpi[0]),
        "kpi_calls_7d": _fmt(w[1]),
        "kpi_calls_30d": _fmt(w[2]),
        "kpi_calls_today": _fmt(w[0]),
        "kpi_avg_mos": _avg(kpi[1]),
        "kpi_pct_below": _pct(kpi[2]),
        "kpi_failed_rate": _pct(kpi[3]),
        "trend_svg": svg,
        "worst": worst,
        "ingest_errors": n_ingest_errors or None,
    }
    return templates.TemplateResponse(request, "dashboard.html", ctx)

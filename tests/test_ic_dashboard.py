import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import psycopg
from app.trend import svg_spark
from tests.util import check

DSN = os.environ["IC_TEST_DSN"]

def run():
    svg = svg_spark([3.9, 4.0, None, 4.2], labels=["d1", "d2", "d3", "d4"])
    check("polyline" in svg, "spark renders polyline")
    conn = psycopg.connect(DSN, autocommit=True)
    kpi = conn.execute(
        "SELECT count(*), AVG(mos_best), AVG((mos_best < 3.5)::int), "
        "AVG((disposition='failed')::int) FROM calls").fetchone()
    check(kpi[0] >= 1, "KPI query returns rows")
    print("test_ic_dashboard: OK")

if __name__ == "__main__":
    run()
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import psycopg
from ingest.loader import load_file
from tests.util import check

DSN = os.environ["IC_TEST_DSN"]

CDR_FILE = (
    "globalCallID_callId,globalCallID_callManagerId,globalCallId_ClusterID,dateTimeOrigination,"
    "dateTimeConnect,dateTimeDisconnect,callingPartyNumber,originalCalledPartyNumber,"
    "origDeviceName,destDeviceName,callingPartyUnicodeLoginUserID,lastRedirectDnLoginUserId,"
    "duration,destCause_value,origLegCallIdentifier,comment,callingPartyNumPartition,finalPartyPartition\n"
    "Integer,Integer,String,Integer,Integer,Integer,String,String,String,String,String,String,"
    "Integer,Integer,Integer,String,String,String\n"
    "300001,1,Cluster-02,1772000000,1772000010,1772000070,55501234567,55509876543,"
    "SEP0A1B2C3D4E5F,SEP0B2C3D4E5F60,jdoe,\\,60,0,700,hello,ACME_Employees,ACME_Employees\n"
)

def run():
    conn = psycopg.connect(DSN, autocommit=True)
    conn.execute("DELETE FROM ingest_log")
    with tempfile.NamedTemporaryFile("w", suffix="_Cluster-02_1_20260301000000_1_CDR.txt", delete=False) as f:
        f.write(CDR_FILE)
        p = f.name
    kind, n, status = load_file(conn, p)
    check(kind == "cdr" and n == 1 and status == "loaded", "first load ok")
    kind, n, status = load_file(conn, p)
    check(status == "duplicate", "re-delivery idempotent")
    row = conn.execute("SELECT calling_party, orig_user_id, raw->>'comment' FROM cdr "
                       "WHERE call_id=300001").fetchone()
    check(row[0] == "55501234567" and row[1] == "jdoe", "extracted columns")
    c = conn.execute("SELECT disposition, duration, calling_party FROM calls WHERE call_id=300001").fetchone()
    check(c[0] == "answered" and c[1] == 60 and c[2] == "55501234567", "calls row materialized")
    print("test_ic_ingest: OK")

if __name__ == "__main__":
    run()
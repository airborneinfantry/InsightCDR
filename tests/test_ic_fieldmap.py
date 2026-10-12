import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ingest.fieldmap import parse_cucm_file, row_get, row_int
from ingest.vqmetrics import parse_var_vq_metrics
from tests.util import check

CDR = (
    "globalCallID_callId,globalCallID_callManagerId,globalCallId_ClusterID,dateTimeOrigination,comment\n"
    "Integer,Integer,String,Integer,String\n"
    "300001,1,Cluster-02,1772000000,note\n"
)

def run():
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(CDR)
        p = f.name
    idx, rows = parse_cucm_file(p)
    check(idx["globalcallid_callid"] == 0, "header lowercased, positional index")
    check(idx["globalcallid_clusterid"] == 2, "casing gotcha neutralized")
    check(row_get(idx, rows[0], "globalCallId_ClusterID") == "Cluster-02", "row_get by any case")
    check(row_get(idx, rows[0], "NotAField") is None, "missing field -> None")
    check(row_int("1772000000") == 1772000000, "row_int")
    check(row_int("\\") is None, "empty marker -> None")
    vq = parse_var_vq_metrics("MOS-LQO=4.12;MOS-CQO=4.31;Codec=G711")
    check(vq["mos_lqo"] == 4.12 and vq["mos_cqo"] == 4.31, "MOS parsed from varVQMetrics")
    print("test_ic_fieldmap: OK")

if __name__ == "__main__":
    run()
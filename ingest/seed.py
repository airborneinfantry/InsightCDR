"""Bulk seed loader: a read-only CDR/CMR corpus -> cdr/cmr/calls/device_daily.

Convention (same as the sibling project's ingest/seed.py): the corpus directory is a
PRESERVED, READ-ONLY corpus — corpus files are never removed, renamed,
modified or quarantined by the seed. ingest_log (unique filename) is the
idempotency marker: a fully completed re-run finds every file already
'loaded' and loads 0 new rows, then still re-runs the batched calls +
device_daily rebuild (harmless and idempotent). A prior 'error' row is a
retry marker, not a terminal state: the seed clears it and re-attempts the
file (the app role has UPDATE/DELETE on ingest_log here).

Because the shared spool loader ingest.loader.load_file() removes the file
it processes (spool semantics), the seed copies each todo file to a scratch
spool dir and loads THE COPY — the corpus itself is never touched, while the
extract/COPY path stays the loader's own (seed and spool worker cannot
drift). Files are iterated CDR first, then CMR, each group alphabetically,
so a partial run leaves a predictable resume point.

Usage (on llmdev):
    IC_DB_DSN=postgres://... python3 ingest/seed.py            # IC_SEED_DIR default
    IC_SEED_DIR=/opt/seed/Cluster-02 IC_SEED_FAST=1 is set internally.
"""
import glob
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest.loader import load_file
from ingest.calls import rebuild_calls
from ingest.daily import refresh_device_daily_batch

SRC = os.environ.get("IC_SEED_DIR", "/opt/seed/Cluster-02")
SCRATCH = os.environ.get("IC_SEED_SCRATCH", "/tmp/insightcdr-seed-spool")
KEYS_PER_BATCH = int(os.environ.get("IC_SEED_KEYS_PER_BATCH", "20000"))


def corpus_files(src):
    """CDR files (sorted), then CMR files (sorted); .txt and .TXT both match."""
    cdrs = set(glob.glob(os.path.join(src, "*CDR.txt"))
               + glob.glob(os.path.join(src, "*CDR.TXT")))
    cmrs = set(glob.glob(os.path.join(src, "*CMR.txt"))
               + glob.glob(os.path.join(src, "*CMR.TXT")))
    return sorted(cdrs), sorted(cmrs)


def main():
    dsn = os.environ["IC_DB_DSN"]
    os.environ["IC_SEED_FAST"] = "1"  # skip per-file rebuilds inside load_file
    conn = psycopg_connect(dsn)
    cdrs, cmrs = corpus_files(SRC)
    files = cdrs + cmrs
    if not files:
        print(f"seed: no CDR/CMR files under {SRC}", flush=True)
        return 1
    loaded = {r[0] for r in conn.execute(
        "SELECT filename FROM ingest_log WHERE status='loaded'").fetchall()}
    todo = [p for p in files if os.path.basename(p) not in loaded]
    print(f"seed: {len(files)} corpus files, {len(loaded)} already loaded, "
          f"{len(todo)} to load", flush=True)
    n_err = 0
    os.makedirs(SCRATCH, exist_ok=True)
    for p in todo:
        base = os.path.basename(p)
        # a prior 'error' row is a retry marker, not a duplicate
        conn.execute("DELETE FROM ingest_log WHERE filename=%s AND status='error'", (base,))
        tmp = os.path.join(SCRATCH, base)
        shutil.copy2(p, tmp)  # corpus is read-only: load the copy, never the original
        kind, n, status = load_file(conn, tmp)
        print(f"  {base}: {kind} {n} {status}", flush=True)
        if status == "error":
            n_err += 1
    if n_err:
        print(f"seed: WARNING {n_err} file(s) errored during load", flush=True)
    rebuild_calls_all(conn)
    refresh_device_daily_batch(conn)
    counts = conn.execute(
        "SELECT (SELECT count(*) FROM cdr), (SELECT count(*) FROM cmr), "
        "(SELECT count(*) FROM calls), (SELECT count(*) FROM ingest_log), "
        "(SELECT count(*) FROM ingest_log WHERE status='loaded')").fetchone()
    print(f"seed complete: cdr={counts[0]} cmr={counts[1]} calls={counts[2]} "
          f"ingest_log={counts[3]} (loaded={counts[4]})", flush=True)
    return 1 if n_err else 0


def rebuild_calls_all(conn):
    """Batched rebuild_calls over ALL distinct stitch keys, keyset-paginated.

    The Task 12 spec's `SELECT DISTINCT ... fetchall()` would buffer the full
    stitch-key list in Python memory (millions of tuples); this walks the
    cdr_stitch_idx with row-value keyset pagination instead, so memory stays
    bounded while the batch size (and the temp-table COPY pattern from
    calls.py) is unchanged.
    """
    import psycopg
    total = 0
    last = ("", -1, -1)
    while True:
        rows = conn.execute(
            "SELECT cluster_id, call_id, call_manager_id FROM cdr "
            "WHERE (cluster_id, call_id, call_manager_id) > (%s, %s, %s) "
            "GROUP BY 1, 2, 3 ORDER BY 1, 2, 3 LIMIT %s",
            (last[0], last[1], last[2], KEYS_PER_BATCH)).fetchall()
        if not rows:
            break
        n = rebuild_calls(conn, rows)
        total += len(rows)
        last = rows[-1]
        print(f"  calls rebuilt: {total} stitch keys (last batch +{n} rows)", flush=True)
    print(f"rebuild_calls done: {total} stitch keys", flush=True)


def psycopg_connect(dsn):
    import psycopg
    return psycopg.connect(dsn, autocommit=True)


if __name__ == "__main__":
    sys.exit(main())

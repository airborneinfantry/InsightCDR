import glob, os, time
from ingest.loader import load_file


def main():
    spool = os.environ.get("IC_SPOOL", "/opt/insightcdr/spool/incoming")
    dsn = os.environ["IC_DB_DSN"]
    import psycopg
    while True:
        try:
            conn = psycopg.connect(dsn, autocommit=True)
            # Case-insensitive .txt matching (M-10c): .txt / .TXT / .Txt all load.
            for path in sorted(p for p in glob.glob(os.path.join(spool, "*"))
                               if p.lower().endswith(".txt")):
                try:
                    load_file(conn, path)
                except Exception as e:
                    print(f"worker: {path}: {e}", flush=True)
            conn.close()
        except Exception as e:
            print(f"worker: db: {e}", flush=True)
        time.sleep(5)


if __name__ == "__main__":
    main()

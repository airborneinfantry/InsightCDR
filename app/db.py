import psycopg


def get_conn(cfg):
    return psycopg.connect(cfg["db_dsn"], autocommit=True)
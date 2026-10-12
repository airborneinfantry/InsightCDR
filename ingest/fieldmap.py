import csv

EMPTY = "\\"  # CUCM lone-backslash = empty field


def parse_cucm_file(path):
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    if len(rows) < 3:
        raise ValueError(f"need >= 3 lines (header, types, data): {len(rows)}")
    headers = [h.strip().lower() for h in rows[0]]
    idx = {h: i for i, h in enumerate(headers)}
    data = [r for r in rows[2:] if r]
    for r in data:
        if len(r) != len(headers):
            raise ValueError(f"row width {len(r)} != header width {len(headers)}")
    return idx, data


def row_get(idx, row, name):
    i = idx.get(name.lower())
    if i is None or i >= len(row):
        return None
    v = row[i].strip()
    return None if v == EMPTY or v == "" else v


def row_int(v):
    if v is None:
        return None
    try:
        return int(v)
    except ValueError:
        return None

def parse_var_vq_metrics(s):
    out = {"mos_lqo": None, "mos_cqo": None}
    if not s:
        return out
    for part in s.split(";"):
        if "=" not in part:
            continue
        k, _, v = part.partition("=")
        k = k.strip().upper()
        if k == "MOS-LQO":
            try:
                out["mos_lqo"] = float(v)
            except ValueError:
                pass
        elif k == "MOS-CQO":
            try:
                out["mos_cqo"] = float(v)
            except ValueError:
                pass
    return out

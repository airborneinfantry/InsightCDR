import html


def svg_spark(values, width=760, height=180, labels=None):
    pts = [(i, v) for i, v in enumerate(values) if v is not None]
    if not pts:
        return "<svg></svg>"
    lo = min(v for _, v in pts)
    hi = max(v for _, v in pts)
    rng = (hi - lo) or 1.0

    def xy(i, v):
        x = 40 + i * (width - 60) / max(len(values) - 1, 1)
        y = 20 + (1 - (v - lo) / rng) * (height - 50)
        return x, y

    poly = " ".join(f"{x:.1f},{y:.1f}" for i, v in pts for x, y in [xy(i, v)])
    out = [f'<svg viewBox="0 0 {width} {height}" class="trend">']
    if lo < 3.5 < hi:
        y35 = 20 + (1 - (3.5 - lo) / rng) * (height - 50)
        out.append(f'<line x1="40" y1="{y35:.1f}" x2="{width}" y2="{y35:.1f}" '
                   f'stroke="#c0392b" stroke-dasharray="4 4" stroke-width="1"/>')
    out.append(f'<polyline fill="none" stroke="#1f6feb" stroke-width="2.5" points="{poly}"/>')
    if labels:
        step = max(len(labels) // 8, 1)
        for i in range(0, len(labels), step):
            x, _ = xy(i, lo)
            out.append(f'<text x="{x:.1f}" y="{height - 4}" font-size="11" '
                       f'text-anchor="middle">{html.escape(str(labels[i]))}</text>')
    out.append("</svg>")
    return "".join(out)

"""The prognosticator quadrant: every roster voice plotted by near-term vs long-term outlook.

    x = 12-month outlook (-5 bearish .. +5 bullish)
    y = 36-month outlook (-5 .. +5, up is bullish)
    dot size = conviction (1-5); ringed dot = changed this run;
    dashed tail = where that voice sat in the previous report.

quadrant_svg() returns a self-contained inline <svg>. Colours are CSS variables so the
chart follows the host page's light/dark theme; pass the palette for the host page.
Used by build_report.py (the weekly report) and publish_site.py (anotherroof.com).
"""
from __future__ import annotations

from html import escape

PALETTES = {
    # the weekly report page (templates/web.html)
    "report": {
        "bull": "var(--bull)", "bear": "var(--bear)", "neutral": "var(--neutral)",
        "ink": "var(--ink)", "soft": "var(--ink-soft)", "faint": "var(--ink-faint)",
        "rule": "var(--rule)", "rule_strong": "var(--rule-strong)", "accent": "var(--accent)",
        "panel": "var(--paper-raised)",
    },
    # anotherroof.com (src/styles/global.css)
    "site": {
        "bull": "var(--band-good)", "bear": "var(--band-extreme)", "neutral": "var(--ink-3)",
        "ink": "var(--ink)", "soft": "var(--ink-2)", "faint": "var(--ink-3)",
        "rule": "var(--line)", "rule_strong": "var(--ink-3)", "accent": "var(--accent)",
        "panel": "var(--surface)",
    },
}

W, H = 760, 690
L, T, S = 110, 46, 540          # plot left, top, side (square)
SPAN = 5.6                      # axis runs -SPAN..+SPAN so +/-5 voices clear the frame
UNIT = S / (2 * SPAN)           # one score point in px
CHAR_W = 7.2                    # generous width of one 11.5px label character (bold, fallback fonts)
LABEL_H = 14


def _px(v: float) -> float:
    return L + (v + SPAN) * UNIT


def _py(v: float) -> float:
    return T + (SPAN - v) * UNIT


def _radius(conviction: float | None) -> float:
    c = conviction if conviction is not None else 3.0
    return 3.5 + 1.6 * max(1.0, min(5.0, c))


def _tone(r: dict) -> str:
    lean = (r.get("s12", 0) or 0) + (r.get("s36", 0) or 0)
    return "bull" if lean > 0 else ("bear" if lean < 0 else "neutral")


def _overlaps(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def quadrant_svg(roster: list[dict], prev_roster: list[dict] | None = None,
                 palette: str = "report", title: str | None = None) -> str:
    P = PALETTES[palette]
    prev = {r["name"]: r for r in (prev_roster or []) if "name" in r}
    people = [r for r in roster if r.get("s12") is not None and r.get("s36") is not None]

    # spread voices that share a coordinate around a small ring so every dot shows
    groups: dict[tuple, list[dict]] = {}
    for r in people:
        groups.setdefault((r["s12"], r["s36"]), []).append(r)
    placed = []
    for (x, y), members in groups.items():
        cx, cy = _px(x), _py(y)
        n = len(members)
        for i, r in enumerate(sorted(members, key=lambda m: m["name"])):
            if n == 1:
                dx = dy = 0.0
            else:
                import math
                ang = -math.pi / 2 + 2 * math.pi * i / n
                dx, dy = 13 * math.cos(ang), 13 * math.sin(ang)
            placed.append({**r, "cx": cx + dx, "cy": cy + dy, "r": _radius(r.get("conviction"))})

    out = []
    a = out.append
    label = escape(title or "Prognosticator quadrant: each voice's 12-month outlook against "
                   "their 36-month outlook, sized by conviction")
    a(f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{label}" '
      f'xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block;'
      f'font-family:\'IBM Plex Mono\',ui-monospace,monospace">')

    # quadrant tints
    half = S / 2
    tints = [
        (L + half, T, P["bull"], 0.07),           # bull now, bull later
        (L, T + half, P["bear"], 0.07),           # bear now, bear later
        (L, T, P["neutral"], 0.035),              # pain now, gain later
        (L + half, T + half, P["neutral"], 0.035),  # party now, pay later
    ]
    for x, y, col, op in tints:
        a(f'<rect x="{x:.1f}" y="{y:.1f}" width="{half:.1f}" height="{half:.1f}" fill="{col}" fill-opacity="{op}"/>')
    a(f'<rect x="{L}" y="{T}" width="{S}" height="{S}" fill="none" stroke="{P["rule"]}"/>')

    # grid + ticks
    for v in range(-4, 5):
        if v == 0:
            continue
        a(f'<line x1="{_px(v):.1f}" y1="{T}" x2="{_px(v):.1f}" y2="{T+S}" stroke="{P["rule"]}" stroke-width="1" stroke-dasharray="2 4"/>')
        a(f'<line x1="{L}" y1="{_py(v):.1f}" x2="{L+S}" y2="{_py(v):.1f}" stroke="{P["rule"]}" stroke-width="1" stroke-dasharray="2 4"/>')
    a(f'<line x1="{_px(0):.1f}" y1="{T}" x2="{_px(0):.1f}" y2="{T+S}" stroke="{P["rule_strong"]}" stroke-width="1.5"/>')
    a(f'<line x1="{L}" y1="{_py(0):.1f}" x2="{L+S}" y2="{_py(0):.1f}" stroke="{P["rule_strong"]}" stroke-width="1.5"/>')
    for v in (-5, -4, -2, 0, 2, 4, 5):
        a(f'<text x="{_px(v):.1f}" y="{T+S+18}" font-size="11" fill="{P["faint"]}" text-anchor="middle">{v:+d}</text>'.replace("+0", "0"))
        a(f'<text x="{L-10}" y="{_py(v)+4:.1f}" font-size="11" fill="{P["faint"]}" text-anchor="end">{v:+d}</text>'.replace("+0", "0"))

    # axis titles
    a(f'<text x="{L+S/2}" y="{T+S+42}" font-size="12" fill="{P["soft"]}" text-anchor="middle">'
      f'next 12 months  ←  bearish · bullish  →</text>')
    a(f'<text transform="translate({L-48} {T+S/2}) rotate(-90)" font-size="12" fill="{P["soft"]}" '
      f'text-anchor="middle">next 36 months  ←  bearish · bullish  →</text>')

    # quadrant names
    q = [
        (L + S, T - 10, "end", "BULL NOW · BULL LATER", P["bull"]),
        (L + 10, T + S - 10, "start", "BEAR NOW · BEAR LATER", P["bear"]),
        (L, T - 10, "start", "PAIN NOW · GAIN LATER", P["faint"]),
        (L + S - 10, T + S - 10, "end", "PARTY NOW · PAY LATER", P["faint"]),
    ]
    for x, y, anchor, text, col in q:
        a(f'<text x="{x}" y="{y}" font-size="10.5" font-weight="600" letter-spacing=".08em" '
          f'fill="{col}" text-anchor="{anchor}">{text}</text>')

    # tails from last report's position
    for p in placed:
        o = prev.get(p["name"])
        if not o or o.get("s12") is None or o.get("s36") is None:
            continue
        if o["s12"] == p["s12"] and o["s36"] == p["s36"]:
            continue
        a(f'<line x1="{_px(o["s12"]):.1f}" y1="{_py(o["s36"]):.1f}" x2="{p["cx"]:.1f}" y2="{p["cy"]:.1f}" '
          f'stroke="{P["accent"]}" stroke-width="1.5" stroke-dasharray="3 3" stroke-opacity=".7"/>')
        a(f'<circle cx="{_px(o["s12"]):.1f}" cy="{_py(o["s36"]):.1f}" r="3" fill="none" '
          f'stroke="{P["accent"]}" stroke-opacity=".7"/>')

    # dots (big first so small ones stay on top)
    boxes = []
    for p in sorted(placed, key=lambda d: -d["r"]):
        col = P[_tone(p)]
        a(f'<circle cx="{p["cx"]:.1f}" cy="{p["cy"]:.1f}" r="{p["r"]:.1f}" fill="{col}" fill-opacity=".8" '
          f'stroke="{P["panel"]}" stroke-width="1.5"><title>{escape(p["name"])}: 12m {p["s12"]:+d}, '
          f'24m {(p.get("s24") or 0):+d}, 36m {p["s36"]:+d}, conviction {p.get("conviction", "?")}</title></circle>')
        if p.get("changed_this_week") or p.get("changed"):
            a(f'<circle cx="{p["cx"]:.1f}" cy="{p["cy"]:.1f}" r="{p["r"]+3.5:.1f}" fill="none" '
              f'stroke="{P["accent"]}" stroke-width="1.5"/>')
        boxes.append((p["cx"] - p["r"], p["cy"] - p["r"], p["cx"] + p["r"], p["cy"] + p["r"]))

    # labels: try right, left, above, below; keep the first spot that collides with nothing
    labels = []
    for p in sorted(placed, key=lambda d: (d["cx"], d["cy"])):
        w = len(p["name"]) * CHAR_W
        r = p["r"] + 4
        cands = [
            (p["cx"] + r, p["cy"] + 4, "start"),
            (p["cx"] - r, p["cy"] + 4, "end"),
            (p["cx"], p["cy"] - r - 2, "middle"),
            (p["cx"], p["cy"] + r + 11, "middle"),
            (p["cx"] + r, p["cy"] - 8, "start"),
            (p["cx"] + r, p["cy"] + 16, "start"),
            (p["cx"] - r, p["cy"] - 8, "end"),
            (p["cx"] - r, p["cy"] + 16, "end"),
        ]
        chosen = None
        for x, y, anchor in cands:
            x0 = x if anchor == "start" else (x - w if anchor == "end" else x - w / 2)
            box = (x0 - 2, y - LABEL_H + 3, x0 + w + 3, y + 3)
            inside = box[0] >= L + 2 and box[2] <= L + S - 2 and box[1] >= T + 2 and box[3] <= T + S - 14
            if inside and not any(_overlaps(box, b) for b in boxes + labels):
                chosen = (x, y, anchor, box)
                break
        if chosen is None:
            x, y, anchor = cands[0]
            chosen = (x, y, anchor, (x, y - LABEL_H, x + w, y))
        x, y, anchor, box = chosen
        labels.append(box)
        weight = "600" if (p.get("changed_this_week") or p.get("changed")) else "400"
        a(f'<text x="{x:.1f}" y="{y:.1f}" font-size="11.5" font-weight="{weight}" fill="{P["ink"]}" '
          f'text-anchor="{anchor}" paint-order="stroke" stroke="{P["panel"]}" stroke-width="3" '
          f'stroke-linejoin="round">{escape(p["name"])}</text>')

    # legend
    ly = H - 10
    a(f'<g font-size="11" fill="{P["faint"]}">')
    a(f'<circle cx="{L+6}" cy="{ly-4}" r="{_radius(1):.1f}" fill="{P["faint"]}" fill-opacity=".6"/>'
      f'<circle cx="{L+26}" cy="{ly-4}" r="{_radius(5):.1f}" fill="{P["faint"]}" fill-opacity=".6"/>'
      f'<text x="{L+44}" y="{ly}">conviction 1 → 5</text>')
    a(f'<circle cx="{L+200}" cy="{ly-4}" r="6" fill="none" stroke="{P["accent"]}" stroke-width="1.5"/>'
      f'<text x="{L+214}" y="{ly}">changed this run</text>')
    if prev:
        a(f'<line x1="{L+350}" y1="{ly-4}" x2="{L+380}" y2="{ly-4}" stroke="{P["accent"]}" '
          f'stroke-width="1.5" stroke-dasharray="3 3"/><text x="{L+388}" y="{ly}">moved from last report</text>')
    a('</g></svg>')
    return "".join(out)


if __name__ == "__main__":  # quick look: python3 scripts/quadrant.py reports/data/report-DATE.json [prev.json]
    import json
    import sys
    cur = json.load(open(sys.argv[1]))["sentiment_snapshot"]["roster"]
    prv = json.load(open(sys.argv[2]))["sentiment_snapshot"]["roster"] if len(sys.argv) > 2 else None
    print(quadrant_svg(cur, prv))

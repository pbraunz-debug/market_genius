"""The prognosticator quadrant: every roster voice plotted by near-term vs long-term outlook.

    x = 12-month outlook (-5 bearish .. +5 bullish)
    y = 36-month outlook (-5 .. +5, up is bullish)
    dot size = conviction (1-5); ringed dot = changed this run.

Two outputs share one layout engine (dot spreading, label placement, axes):

    quadrant_svg(roster, prev_roster)   one run as a static inline <svg>, with dashed
                                        tails from the previous run. Used in each weekly
                                        report page (build_report.py).
    player(runs)                        every run as frames for the animated, slider-driven
                                        quadrant on anotherroof.com. write_player(site) builds
                                        it from the site's src/data/froth/*.json (publish_site.py).

Colours are CSS variables so the chart follows the host page's light/dark theme.
"""
from __future__ import annotations

import json
import math
from html import escape
from pathlib import Path

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
TONES = ("bull", "bear", "neutral")
ANCHORS = ("start", "end", "middle")


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


def _changed(r: dict) -> bool:
    return bool(r.get("changed_this_week") or r.get("changed"))


def _overlaps(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


# ---------------------------------------------------------------------------
# layout: where every dot and label goes for one run
# ---------------------------------------------------------------------------

def layout(roster: list[dict]) -> list[dict]:
    """Place each voice: spread shared coordinates on a ring, then label without collisions."""
    people = [r for r in roster if r.get("s12") is not None and r.get("s36") is not None]
    groups: dict[tuple, list[dict]] = {}
    for r in people:
        groups.setdefault((r["s12"], r["s36"]), []).append(r)
    placed = []
    for (x, y), members in groups.items():
        n = len(members)
        for i, r in enumerate(sorted(members, key=lambda m: m["name"])):
            ang = -math.pi / 2 + 2 * math.pi * i / n
            dx, dy = (0.0, 0.0) if n == 1 else (13 * math.cos(ang), 13 * math.sin(ang))
            placed.append({**r, "cx": _px(x) + dx, "cy": _py(y) + dy,
                           "r": _radius(r.get("conviction")), "tone": _tone(r)})

    boxes = [(p["cx"] - p["r"], p["cy"] - p["r"], p["cx"] + p["r"], p["cy"] + p["r"]) for p in placed]
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
        p["lx"], p["ly"], p["anchor"], box = chosen
        labels.append(box)
    return placed


# ---------------------------------------------------------------------------
# shared drawing
# ---------------------------------------------------------------------------

def _open(a, title: str | None, extra: str = "") -> None:
    label = escape(title or "Prognosticator quadrant: each voice's 12-month outlook against "
                   "their 36-month outlook, sized by conviction")
    a(f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{label}" {extra}'
      f'xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block;'
      f'font-family:\'IBM Plex Mono\',ui-monospace,monospace">')


def _scaffold(a, P: dict) -> None:
    half = S / 2
    for x, y, col, op in [
        (L + half, T, P["bull"], 0.07),             # bull now, bull later
        (L, T + half, P["bear"], 0.07),             # bear now, bear later
        (L, T, P["neutral"], 0.035),                # pain now, gain later
        (L + half, T + half, P["neutral"], 0.035),  # party now, pay later
    ]:
        a(f'<rect x="{x:.1f}" y="{y:.1f}" width="{half:.1f}" height="{half:.1f}" fill="{col}" fill-opacity="{op}"/>')
    a(f'<rect x="{L}" y="{T}" width="{S}" height="{S}" fill="none" stroke="{P["rule"]}"/>')
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
    a(f'<text x="{L+S/2}" y="{T+S+42}" font-size="12" fill="{P["soft"]}" text-anchor="middle">'
      f'next 12 months  ←  bearish · bullish  →</text>')
    a(f'<text transform="translate({L-48} {T+S/2}) rotate(-90)" font-size="12" fill="{P["soft"]}" '
      f'text-anchor="middle">next 36 months  ←  bearish · bullish  →</text>')
    for x, y, anchor, text, col in [
        (L + S, T - 10, "end", "BULL NOW · BULL LATER", P["bull"]),
        (L + 10, T + S - 10, "start", "BEAR NOW · BEAR LATER", P["bear"]),
        (L, T - 10, "start", "PAIN NOW · GAIN LATER", P["faint"]),
        (L + S - 10, T + S - 10, "end", "PARTY NOW · PAY LATER", P["faint"]),
    ]:
        a(f'<text x="{x}" y="{y}" font-size="10.5" font-weight="600" letter-spacing=".08em" '
          f'fill="{col}" text-anchor="{anchor}">{text}</text>')


def _legend(a, P: dict, tail_label: str | None) -> None:
    ly = H - 10
    a(f'<g font-size="11" fill="{P["faint"]}">')
    a(f'<circle cx="{L+6}" cy="{ly-4}" r="{_radius(1):.1f}" fill="{P["faint"]}" fill-opacity=".6"/>'
      f'<circle cx="{L+26}" cy="{ly-4}" r="{_radius(5):.1f}" fill="{P["faint"]}" fill-opacity=".6"/>'
      f'<text x="{L+44}" y="{ly}">conviction 1 → 5</text>')
    a(f'<circle cx="{L+200}" cy="{ly-4}" r="6" fill="none" stroke="{P["accent"]}" stroke-width="1.5"/>'
      f'<text x="{L+214}" y="{ly}">changed this run</text>')
    if tail_label:
        a(f'<line x1="{L+350}" y1="{ly-4}" x2="{L+380}" y2="{ly-4}" stroke="{P["accent"]}" '
          f'stroke-width="1.5" stroke-dasharray="3 3"/><text x="{L+388}" y="{ly}">{tail_label}</text>')
    a('</g>')


def _label_attrs(P: dict) -> str:
    return (f'font-size="11.5" fill="{P["ink"]}" paint-order="stroke" stroke="{P["panel"]}" '
            f'stroke-width="3" stroke-linejoin="round"')


# ---------------------------------------------------------------------------
# one run, static
# ---------------------------------------------------------------------------

def quadrant_svg(roster: list[dict], prev_roster: list[dict] | None = None,
                 palette: str = "report", title: str | None = None) -> str:
    P = PALETTES[palette]
    prev = {r["name"]: r for r in (prev_roster or []) if "name" in r}
    placed = layout(roster)
    out: list[str] = []
    a = out.append
    _open(a, title)
    _scaffold(a, P)

    for p in placed:  # tails from the previous run's position
        o = prev.get(p["name"])
        if not o or o.get("s12") is None or o.get("s36") is None:
            continue
        if o["s12"] == p["s12"] and o["s36"] == p["s36"]:
            continue
        a(f'<line x1="{_px(o["s12"]):.1f}" y1="{_py(o["s36"]):.1f}" x2="{p["cx"]:.1f}" y2="{p["cy"]:.1f}" '
          f'stroke="{P["accent"]}" stroke-width="1.5" stroke-dasharray="3 3" stroke-opacity=".7"/>')
        a(f'<circle cx="{_px(o["s12"]):.1f}" cy="{_py(o["s36"]):.1f}" r="3" fill="none" '
          f'stroke="{P["accent"]}" stroke-opacity=".7"/>')

    for p in sorted(placed, key=lambda d: -d["r"]):  # big first so small dots stay on top
        a(f'<circle cx="{p["cx"]:.1f}" cy="{p["cy"]:.1f}" r="{p["r"]:.1f}" fill="{P[p["tone"]]}" fill-opacity=".8" '
          f'stroke="{P["panel"]}" stroke-width="1.5"><title>{escape(p["name"])}: 12m {p["s12"]:+d}, '
          f'24m {(p.get("s24") or 0):+d}, 36m {p["s36"]:+d}, conviction {p.get("conviction", "?")}</title></circle>')
        if _changed(p):
            a(f'<circle cx="{p["cx"]:.1f}" cy="{p["cy"]:.1f}" r="{p["r"]+3.5:.1f}" fill="none" '
              f'stroke="{P["accent"]}" stroke-width="1.5"/>')
    for p in placed:
        weight = "600" if _changed(p) else "400"
        a(f'<text x="{p["lx"]:.1f}" y="{p["ly"]:.1f}" font-weight="{weight}" text-anchor="{p["anchor"]}" '
          f'{_label_attrs(P)}>{escape(p["name"])}</text>')

    _legend(a, P, "moved from last report" if prev else None)
    a('</svg>')
    return "".join(out)


# ---------------------------------------------------------------------------
# every run, animated (anotherroof.com)
# ---------------------------------------------------------------------------

def player(runs: list[dict], palette: str = "site") -> dict:
    """runs: [{"date": "YYYY-MM-DD", "roster": [...]}, ...] oldest first.

    Returns {"svg": scaffold with one group per voice, "dates": [...], "people": [...]}.
    Each person's "f" holds one entry per run: [cx, cy, r, tone, label_dx, label_dy, anchor,
    changed, s12, s24, s36, conviction], or null when they were not on the roster that run.
    The page script moves the groups between frames; CSS transitions do the animating.
    """
    P = PALETTES[palette]
    runs = sorted(runs, key=lambda r: r["date"])
    frames = [{p["name"]: p for p in layout(r["roster"])} for r in runs]
    names = sorted({n for f in frames for n in f})
    people = []
    for name in names:
        f = []
        for fr in frames:
            p = fr.get(name)
            if p is None:
                f.append(None)
                continue
            f.append([round(p["cx"], 1), round(p["cy"], 1), round(p["r"], 1), TONES.index(p["tone"]),
                      round(p["lx"] - p["cx"], 1), round(p["ly"] - p["cy"], 1), ANCHORS.index(p["anchor"]),
                      1 if _changed(p) else 0, p["s12"], p.get("s24"), p["s36"], p.get("conviction")])
        people.append({"name": name, "f": f})

    out: list[str] = []
    a = out.append
    _open(a, "Prognosticator quadrant across every run: use the slider to move between runs",
          'class="qplayer-svg" ')
    _scaffold(a, P)
    a('<g class="q-trails">')
    for i, _ in enumerate(people):
        a(f'<polyline class="q-trail" data-i="{i}" points="" fill="none" stroke="{P["accent"]}" '
          f'stroke-width="1.5" stroke-dasharray="3 3" stroke-opacity=".55"/>')
    a('</g><g class="q-people">')
    last = len(runs) - 1
    for i, person in enumerate(people):
        fr = person["f"][last] or next((x for x in reversed(person["f"]) if x), None)
        cx, cy, r, tone, ldx, ldy, anc, ch = fr[:8]
        a(f'<g class="q-p" data-i="{i}" style="transform:translate({cx}px,{cy}px)">'
          f'<circle class="q-ring" r="{r+3.5}" fill="none" stroke="{P["accent"]}" stroke-width="1.5" '
          f'style="opacity:{1 if ch else 0}"/>'
          f'<circle class="q-dot q-{TONES[tone]}" r="{r}" fill-opacity=".8" stroke="{P["panel"]}" stroke-width="1.5">'
          f'<title>{escape(person["name"])}</title></circle>'
          f'<g class="q-l" style="transform:translate({ldx}px,{ldy}px)"><text class="q-t" text-anchor="{ANCHORS[anc]}" '
          f'font-weight="{600 if ch else 400}" {_label_attrs(P)}>{escape(person["name"])}</text></g></g>')
    a('</g>')
    _legend(a, P, "path across runs")
    a('</svg>')
    return {"svg": "".join(out), "dates": [r["date"] for r in runs], "people": people,
            "tones": {t: P[t] for t in TONES}}


def write_player(site: Path) -> Path | None:
    """Rebuild src/data/froth/quadrant-player.json from every run's roster on the site."""
    data = site / "src" / "data" / "froth"
    runs = []
    for f in sorted(data.glob("*.json")):
        if f.name == "quadrant-player.json":
            continue
        d = json.loads(f.read_text())
        if d.get("roster"):
            runs.append({"date": d["date"], "roster": d["roster"]})
    if not runs:
        return None
    out = data / "quadrant-player.json"
    out.write_text(json.dumps(player(runs), separators=(",", ":")))
    return out


if __name__ == "__main__":  # quick look: python3 scripts/quadrant.py reports/data/report-DATE.json [prev.json]
    import sys
    cur = json.load(open(sys.argv[1]))["sentiment_snapshot"]["roster"]
    prv = json.load(open(sys.argv[2]))["sentiment_snapshot"]["roster"] if len(sys.argv) > 2 else None
    print(quadrant_svg(cur, prv))

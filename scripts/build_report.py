#!/usr/bin/env python3
"""
build_report.py — froth dashboard builder.

Reads the methodology in FRAMEWORK.md (weights + anchors mirrored in the INDICATORS /
ANCHORS blocks below), pulls the Shiller S&P dataset and FRED series, merges the
supplemental research the weekly routine writes to data/weekly_inputs.json, scores the
eight indicators, and renders:

    reports/report-YYYY-MM-DD.md        human-readable archive (committed)
    reports/data/report-YYYY-MM-DD.json machine-readable archive (committed, used for diffs)
    data/sentiment_current.json         rolling sentiment state (committed)
    out/email.html                      rendered email body (gitignored)
    out/meta.json                       subject-line inputs for scripts/send_email.sh

Degrade gracefully, never silently: any source that fails is scored from cache, then from
routine-supplied fallbacks, then from the previous report (carried forward and marked
"stale (n weeks)"), and only then dropped — with the weights renormalised over what is
left and every gap listed in the email.

Usage:
    python scripts/build_report.py [--date YYYY-MM-DD] [--offline] [--no-write]
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import math
import os
import statistics
import sys
import traceback
from pathlib import Path

import requests
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "cache"
REPORTS = ROOT / "reports"
REPORT_DATA = REPORTS / "data"
OUT = ROOT / "out"
TEMPLATES = ROOT / "templates"

SHILLER_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500/main/data/data.csv"
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
HTTP_TIMEOUT = 45

# ---------------------------------------------------------------------------
# Framework constants — keep in sync with FRAMEWORK.md sections 1 and 2.
# ---------------------------------------------------------------------------

INDICATORS = [
    ("cape",       "Shiller CAPE",              0.20),
    ("buffett",    "Buffett Indicator",         0.15),
    ("ecy",        "Excess CAPE Yield",         0.15),
    ("trend",      "Price vs 10-year trend",    0.10),
    ("momentum",   "12-month momentum",         0.10),
    ("hy_spread",  "High-yield credit spreads", 0.10),
    ("curve",      "Yield curve 10y-2y",        0.10),
    ("fiscal",     "Fiscal / debt trajectory",  0.10),
]

# piecewise-linear anchor tables: [(x, score), ...] with x ascending
ANCHORS = {
    # market cap / GDP, percent
    "buffett":       [(50, 1.0), (85, 3.0), (120, 4.0), (150, 4.5), (200, 5.0)],
    # excess CAPE yield, decimal (0.03 == +3%)
    "ecy":           [(-0.01, 5.0), (0.0, 4.5), (0.01, 4.0), (0.02, 3.0), (0.03, 2.0), (0.05, 1.0)],
    # ICE BofA HY OAS, basis points
    "hy_spread":     [(200, 5.0), (271, 4.5), (500, 2.5), (800, 1.0)],
    # 10y-2y while inverted or at zero, percentage points
    "curve_inv":     [(-1.50, 4.5), (-0.50, 4.0), (0.0, 3.5)],
    # 10y-2y positive with no inversion in trailing 36 months
    "curve_steep":   [(0.0, 2.5), (1.0, 2.0), (2.0, 1.0)],
    # federal deficit as % of GDP (positive number = deficit)
    "fiscal":        [(2.0, 1.5), (4.0, 3.0), (6.0, 4.5), (8.0, 5.0)],
}

BANDS = [
    (2.5, "deploy aggressively", "#1a7f37", "green"),
    (3.5, "DCA mode", "#b58900", "yellow"),
    (4.5, "frothy / hold dry powder", "#c05621", "orange"),
    (99.0, "extreme", "#b42318", "red"),
]

BACKTEST_LINE = (
    "At composite 4.5+, historically a 40% probability that a 20%+ real decline begins "
    "within 36 months (29% for 30%+) - but 4.5+ zones also produced the longest melt-ups. "
    "The signal is regime, not timing."
)

DEPLOYMENT_RULES = [
    "Base DCA continues regardless of score.",
    "Composite drops below 3.5 -> double the monthly DCA clip.",
    "Composite below 2.5 -> deploy remaining dry powder aggressively.",
    "HY spreads widening past 500bps alongside an equity drawdown -> begin staged dry-powder "
    "deployment: a tranche at -10% S&P drawdown, more at -15%/-20%, the bulk at -20%+ with "
    "spreads confirming.",
]

# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

GAPS: list[str] = []


def gap(msg: str) -> None:
    if msg not in GAPS:
        GAPS.append(msg)
    print(f"  [gap] {msg}", file=sys.stderr)


def clamp(x: float, lo: float = 1.0, hi: float = 5.0) -> float:
    return max(lo, min(hi, x))


def interp(anchors: list[tuple[float, float]], x: float) -> float:
    """Piecewise-linear interpolation across an anchor table, clamped at both ends."""
    if x <= anchors[0][0]:
        return clamp(anchors[0][1])
    if x >= anchors[-1][0]:
        return clamp(anchors[-1][1])
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        if x0 <= x <= x1:
            t = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
            return clamp(y0 + t * (y1 - y0))
    return clamp(anchors[-1][1])


def pct_rank(values: list[float], x: float) -> float:
    """Expanding-window percentile of x within the full history `values` (0..1)."""
    n = len(values)
    if n == 0:
        return 0.5
    below = sum(1 for v in values if v < x)
    equal = sum(1 for v in values if v == x)
    return (below + 0.5 * equal) / n


def pct_score(values: list[float], x: float) -> float:
    """FRAMEWORK 1.1: percentile * 4 + 1."""
    return clamp(pct_rank(values, x) * 4.0 + 1.0)


def weeks_since(iso: str | None, today: dt.date) -> int | None:
    if not iso:
        return None
    try:
        d = dt.date.fromisoformat(str(iso)[:10])
    except ValueError:
        return None
    return max(0, (today - d).days // 7)


def band_for(score: float) -> tuple[str, str, str]:
    for threshold, label, color, name in BANDS:
        if score < threshold:
            return label, color, name
    return BANDS[-1][1], BANDS[-1][2], BANDS[-1][3]


def ordinal(n: float) -> str:
    i = int(round(n))
    suffix = "th" if 10 <= i % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(i % 10, "th")
    return f"{i}{suffix}"


def fmt(x, digits=2, suffix=""):
    if x is None:
        return "n/a"
    return f"{x:,.{digits}f}{suffix}"


# ---------------------------------------------------------------------------
# fetch layer (cache-backed)
# ---------------------------------------------------------------------------


def cache_write(name: str, text: str) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / name).write_text(text)
    (CACHE / f"{name}.meta.json").write_text(
        json.dumps({"fetched_at": dt.date.today().isoformat()})
    )


def cache_read(name: str) -> tuple[str | None, str | None]:
    p = CACHE / name
    if not p.exists():
        return None, None
    meta_p = CACHE / f"{name}.meta.json"
    fetched = None
    if meta_p.exists():
        try:
            fetched = json.loads(meta_p.read_text()).get("fetched_at")
        except json.JSONDecodeError:
            fetched = None
    return p.read_text(), fetched


def fetch_shiller(today: dt.date, offline: bool) -> tuple[str | None, str, int | None]:
    """Returns (csv_text, source_label, stale_weeks)."""
    if not offline:
        try:
            r = requests.get(SHILLER_URL, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            cache_write("shiller.csv", r.text)
            return r.text, "Shiller dataset (live)", 0
        except Exception as exc:  # noqa: BLE001 - never crash the pipeline
            gap(f"Shiller CSV fetch failed ({exc.__class__.__name__}); trying cache.")
    text, fetched = cache_read("shiller.csv")
    if text:
        stale = weeks_since(fetched, today)
        if stale:
            gap(f"Shiller dataset served from cache, stale ({stale} weeks).")
        return text, f"Shiller dataset (cache {fetched or 'unknown'})", stale
    gap("Shiller dataset unavailable and no cache: CAPE, ECY, trend and momentum dropped.")
    return None, "unavailable", None


def fetch_fred(series_id: str, today: dt.date, offline: bool, years: int = 6):
    """Returns (observations [(date, value)], source_label, stale_weeks)."""
    key = os.environ.get("FRED_API_KEY", "").strip()
    name = f"fred_{series_id}.json"
    if not offline and key:
        start = (today - dt.timedelta(days=int(365.25 * years))).isoformat()
        try:
            r = requests.get(
                FRED_URL,
                params={
                    "series_id": series_id,
                    "api_key": key,
                    "file_type": "json",
                    "observation_start": start,
                },
                timeout=HTTP_TIMEOUT,
            )
            r.raise_for_status()
            obs = [
                (o["date"], float(o["value"]))
                for o in r.json().get("observations", [])
                if o.get("value") not in (".", "", None)
            ]
            if obs:
                cache_write(name, json.dumps(obs))
                return obs, f"FRED {series_id} (live)", 0
            gap(f"FRED {series_id} returned no usable observations.")
        except Exception as exc:  # noqa: BLE001
            gap(f"FRED {series_id} fetch failed ({exc.__class__.__name__}); trying cache.")
    elif not offline and not key:
        gap("FRED_API_KEY not set: FRED series served from cache or routine fallbacks.")

    text, fetched = cache_read(name)
    if text:
        try:
            obs = [(d, float(v)) for d, v in json.loads(text)]
            stale = weeks_since(fetched, today)
            if stale:
                gap(f"FRED {series_id} served from cache, stale ({stale} weeks).")
            return obs, f"FRED {series_id} (cache {fetched or 'unknown'})", stale
        except (json.JSONDecodeError, TypeError, ValueError):
            gap(f"FRED {series_id} cache unreadable.")
    return [], "unavailable", None


def latest_obs(obs, on_or_before: dt.date | None = None):
    """Latest (date, value) at or before a cutoff."""
    if not obs:
        return None, None
    rows = sorted(obs)
    if on_or_before is not None:
        rows = [r for r in rows if dt.date.fromisoformat(r[0]) <= on_or_before]
    if not rows:
        return None, None
    return rows[-1][0], rows[-1][1]


# ---------------------------------------------------------------------------
# Shiller dataset processing
# ---------------------------------------------------------------------------


def parse_shiller(text: str) -> list[dict]:
    rows = []
    for r in csv.DictReader(io.StringIO(text)):

        def num(key):
            try:
                v = float(r.get(key, "") or 0)
            except ValueError:
                return None
            return v if v > 0 else None

        if not r.get("Date"):
            continue
        rows.append(
            {
                "date": r["Date"][:10],
                "sp500": num("SP500"),
                "cpi": num("Consumer Price Index"),
                "real_price_csv": num("Real Price"),
                "real_earnings_csv": num("Real Earnings"),
                "long_rate": num("Long Interest Rate"),
            }
        )
    return rows


def build_series(rows: list[dict], cpi_obs, inputs: dict | None = None) -> dict:
    """Real price / CAPE / trend / momentum series, all in the latest CPI base."""
    # 1. CPI: extend the (stale) Shiller CPI column with FRED CPIAUCSL, level-spliced.
    cpi = {r["date"]: r["cpi"] for r in rows if r["cpi"]}
    shiller_cpi_last_date = max(cpi) if cpi else None
    cpi_extended_to = None
    if cpi_obs and shiller_cpi_last_date:
        fred_cpi = {d[:8] + "01": v for d, v in cpi_obs}
        overlap = [d for d in fred_cpi if d in cpi]
        if overlap:
            anchor = max(overlap)
            factor = cpi[anchor] / fred_cpi[anchor]
            for d, v in sorted(fred_cpi.items()):
                if d > shiller_cpi_last_date:
                    cpi[d] = v * factor
                    cpi_extended_to = d
        else:
            gap("CPI splice skipped: no overlap between Shiller CPI and FRED CPIAUCSL.")

    if not cpi:
        return {}

    # 1b. The price column outlives the CPI column by years. Extrapolate CPI forward so
    #     recent months are not silently dropped from every real series.
    cpi_assumed_from = None
    last_cpi_date = max(cpi)
    future = sorted(r["date"] for r in rows if r["date"] > last_cpi_date and r["sp500"])
    if future:
        rate = ((inputs or {}).get("cpi_fallback") or {}).get("annual_rate_pct")
        try:
            monthly = (1.0 + float(rate) / 100.0) ** (1.0 / 12.0) if rate else 1.0
        except (TypeError, ValueError):
            rate, monthly = None, 1.0
        v = cpi[last_cpi_date]
        for d in future:
            v *= monthly
            cpi[d] = v
        cpi_assumed_from = last_cpi_date
        if rate:
            gap(f"CPI extrapolated from {last_cpi_date} to {future[-1]} at the "
                f"routine-supplied {float(rate):.1f}%/yr run-rate; real values are approximate.")
        else:
            gap(f"CPI carried flat from {last_cpi_date} to {future[-1]} (no FRED CPIAUCSL, no "
                "cpi_fallback.annual_rate_pct): real prices understate inflation since then, "
                "which biases CAPE and the trend/momentum percentiles HIGH.")

    base_date = max(cpi)
    base_cpi = cpi[base_date]

    # 2. The CSV's own real columns are expressed in the CPI base of its last valid CPI
    #    month. Recover that base empirically and rebase to ours.
    ratios = [
        (r["real_price_csv"] / r["sp500"]) * r["cpi"]
        for r in rows
        if r["real_price_csv"] and r["sp500"] and r["cpi"]
    ]
    csv_base_cpi = statistics.median(ratios) if ratios else base_cpi
    rebase = base_cpi / csv_base_cpi

    # 2b. The Shiller CSV is monthly and lags by weeks. If the routine supplied a current
    #     index level, use it: same month replaces that month's close, a later month is
    #     appended (which keeps the 12-month momentum lookback exactly 12 rows back).
    spx = (inputs or {}).get("spx_current") or {}
    spx_level, spx_date = spx.get("level"), spx.get("date")
    if spx_level and spx_date:
        try:
            month_key = str(spx_date)[:8] + "01"
            last_row_date = max(r["date"] for r in rows if r["sp500"])
            if month_key <= last_row_date:
                for r in rows:
                    if r["date"] == month_key:
                        r["sp500"] = float(spx_level)
            else:
                rows.append({"date": month_key, "sp500": float(spx_level), "cpi": None,
                             "real_price_csv": None, "real_earnings_csv": None,
                             "long_rate": None})
                cpi.setdefault(month_key, cpi[max(cpi)])
        except (TypeError, ValueError):
            gap("spx_current in weekly_inputs.json is malformed; ignored.")
            spx_level, spx_date = None, None

    dates, real_price, real_earn = [], [], []
    for r in rows:
        if not r["sp500"]:
            continue
        c = cpi.get(r["date"])
        if not c:
            continue
        dates.append(r["date"])
        real_price.append(r["sp500"] * base_cpi / c)
        real_earn.append(r["real_earnings_csv"] * rebase if r["real_earnings_csv"] else None)

    # 3. Trailing 10-year real earnings average; carried forward once the column dies.
    e10, e10_vintage, last_e10 = [], None, None
    for i in range(len(dates)):
        window = real_earn[max(0, i - 119) : i + 1]
        if len(window) == 120 and all(v is not None for v in window):
            last_e10 = sum(window) / 120.0
            e10_vintage = dates[i]
        e10.append(last_e10)

    cape = [
        (real_price[i] / e10[i]) if (e10[i] and e10[i] > 0) else None for i in range(len(dates))
    ]

    trend_ratio = []
    for i in range(len(dates)):
        window = real_price[max(0, i - 119) : i + 1]
        trend_ratio.append(real_price[i] / (sum(window) / len(window)) if len(window) == 120 else None)

    momentum = [
        (real_price[i] / real_price[i - 12] - 1.0) if i >= 12 and real_price[i - 12] else None
        for i in range(len(dates))
    ]

    # trailing 10-year realised CPI inflation, annualised
    infl_10y = None
    sorted_cpi = sorted(cpi.items())
    if len(sorted_cpi) > 120:
        c_now = sorted_cpi[-1][1]
        c_then = sorted_cpi[-121][1]
        if c_then > 0:
            infl_10y = (c_now / c_then) ** 0.1 - 1.0

    return {
        "dates": dates,
        "real_price": real_price,
        "cape": cape,
        "trend_ratio": trend_ratio,
        "momentum": momentum,
        "cpi_base_date": base_date,
        "cpi_extended_to": cpi_extended_to,
        "cpi_assumed_from": cpi_assumed_from,
        "shiller_cpi_last": shiller_cpi_last_date,
        "earnings_vintage": e10_vintage,
        "infl_10y": infl_10y,
        "price_date": spx_date if spx_level else (dates[-1] if dates else None),
        "price_level": float(spx_level) if spx_level else (rows[-1]["sp500"] if rows else None),
        "price_source": ("live index level supplied by the routine" if spx_level
                         else "Shiller CSV monthly close"),
        "long_rate_last": next(
            (r["long_rate"] for r in reversed(rows) if r["long_rate"]), None
        ),
    }


# ---------------------------------------------------------------------------
# supplemental inputs + carry-forward
# ---------------------------------------------------------------------------


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        gap(f"{path.name} is not valid JSON ({exc.msg}); ignored.")
        return default


def previous_reports(today: dt.date) -> list[dict]:
    out = []
    if REPORT_DATA.exists():
        for p in sorted(REPORT_DATA.glob("report-*.json")):
            d = load_json(p, None)
            if d and d.get("date") and d["date"] < today.isoformat():
                out.append(d)
    return out


def pick_prev(reports: list[dict]):
    return reports[-1] if reports else None


def pick_four_weeks_ago(reports: list[dict], today: dt.date):
    if not reports:
        return None
    target = today - dt.timedelta(days=28)
    return min(
        reports,
        key=lambda r: abs((dt.date.fromisoformat(r["date"]) - target).days),
    )


def carried(prev: dict | None, key: str, today: dt.date):
    """Pull an indicator's raw value from the previous report and age it."""
    if not prev:
        return None, None
    ind = (prev.get("indicators") or {}).get(key) or {}
    if ind.get("value") is None:
        return None, None
    as_of = ind.get("as_of") or prev.get("date")
    return ind["value"], weeks_since(as_of, today)


# ---------------------------------------------------------------------------
# indicator scoring
# ---------------------------------------------------------------------------


def ind(key, name, weight, value, display, score, note, source, as_of=None, stale=None):
    return {
        "key": key,
        "name": name,
        "weight": weight,
        "value": value,
        "display": display,
        "score": score,
        "note": note,
        "source": source,
        "as_of": as_of,
        "stale_weeks": stale,
    }


def score_indicators(series, fred, inputs, prev, today) -> dict:
    W = dict((k, w) for k, _, w in ((k, n, w) for k, n, w in INDICATORS))
    N = dict((k, n) for k, n, _ in INDICATORS)
    fallback = inputs.get("fred_fallback", {}) or {}
    out: dict[str, dict] = {}

    # ---- 1. CAPE -----------------------------------------------------------
    cape_hist = [c for c in (series.get("cape") or []) if c]
    computed = cape_hist[-1] if cape_hist else None
    web = (inputs.get("cape_web") or {})
    web_val = web.get("value")
    headline = web_val if web_val else computed
    if headline:
        note_bits = []
        if web_val and computed:
            divergence = abs(web_val - computed)
            note_bits.append(f"web {web_val:.1f} vs computed {computed:.1f}")
            if divergence > 1.5:
                note_bits.append(f"divergence {divergence:.1f} pts - earnings vintage suspect")
        carried_months = 0
        if series.get("earnings_vintage") and series.get("price_date"):
            ev = dt.date.fromisoformat(series["earnings_vintage"])
            pd_ = dt.date.fromisoformat(series["price_date"])
            carried_months = (pd_.year - ev.year) * 12 + (pd_.month - ev.month)
        if series.get("earnings_vintage"):
            note_bits.append(
                f"10yr real earnings carried from {series['earnings_vintage']}"
                + (f" ({carried_months} months)" if carried_months else "")
            )
        if carried_months > 12 and not web_val:
            gap(
                f"CAPE uses real earnings frozen at {series['earnings_vintage']} ({carried_months} "
                "months) with no web cross-check, so the printed value is an UPPER BOUND - price "
                "has moved, the earnings denominator has not. Supply cape_web in "
                "data/weekly_inputs.json to fix this."
            )
            note_bits.append("upper bound: no web cross-check this run")
        out["cape"] = ind(
            "cape", N["cape"], W["cape"], headline, f"{headline:.1f}",
            pct_score(cape_hist, headline),
            f"{ordinal(pct_rank(cape_hist, headline) * 100)} pct since 1881"
            + ("; " + "; ".join(note_bits) if note_bits else ""),
            web.get("source", "Shiller CSV") if web_val else "Shiller CSV",
            web.get("as_of") or series.get("price_date"),
        )
    else:
        gap("CAPE could not be computed (no Shiller series, no web cross-check).")

    # ---- 2. Buffett indicator ---------------------------------------------
    b = inputs.get("buffett_indicator") or {}
    bval, bstale, bsrc, basof = b.get("value"), 0, b.get("source", "web"), b.get("as_of")
    if bval is None:
        bval, bstale = carried(prev, "buffett", today)
        bsrc, basof = "carried from previous report", (prev or {}).get("date")
        if bval is not None:
            gap(f"Buffett indicator not supplied this week; carried forward (stale {bstale} weeks).")
    if bval is not None:
        out["buffett"] = ind(
            "buffett", N["buffett"], W["buffett"], bval, f"{bval:.0f}% of GDP",
            interp(ANCHORS["buffett"], bval),
            "median ~85%, dotcom peak 146-172%; 200%+ is the top of the historical range",
            bsrc, basof, bstale,
        )
    else:
        gap("Buffett indicator missing: weight redistributed.")

    # ---- 3. Excess CAPE yield ---------------------------------------------
    dgs10_date, dgs10 = latest_obs(fred.get("DGS10"))
    if dgs10 is None and fallback.get("DGS10") is not None:
        dgs10, dgs10_date = float(fallback["DGS10"]), fallback.get("as_of")
    if dgs10 is None and series.get("long_rate_last"):
        dgs10, dgs10_date = series["long_rate_last"], series.get("shiller_cpi_last")
        gap("10y nominal yield fell back to the Shiller long-rate column (stale).")
    infl = series.get("infl_10y")
    cape_for_ecy = out.get("cape", {}).get("value")
    if cape_for_ecy and dgs10 is not None and infl is not None:
        real10 = dgs10 / 100.0 - infl
        ecy = 1.0 / cape_for_ecy - real10
        out["ecy"] = ind(
            "ecy", N["ecy"], W["ecy"], ecy, f"{ecy * 100:+.2f}%",
            interp(ANCHORS["ecy"], ecy),
            f"1/CAPE {100 / cape_for_ecy:.2f}% - real 10y {real10 * 100:.2f}% "
            f"(nominal {dgs10:.2f}% - trailing 10y CPI {infl * 100:.2f}%)",
            "derived: FRED DGS10 + Shiller CPI", dgs10_date,
        )
    else:
        gap("Excess CAPE yield unavailable (needs CAPE + 10y nominal + trailing CPI).")

    # ---- 4. Price vs 10-year trend ----------------------------------------
    tr = [t for t in (series.get("trend_ratio") or []) if t]
    if tr:
        cur = tr[-1]
        out["trend"] = ind(
            "trend", N["trend"], W["trend"], cur, f"{cur:.2f}x",
            pct_score(tr, cur),
            f"real price vs its 120-month average; {ordinal(pct_rank(tr, cur) * 100)} pct historically",
            "Shiller CSV", series.get("price_date"),
        )
    else:
        gap("Price-vs-trend unavailable.")

    # ---- 5. 12-month momentum ---------------------------------------------
    mo = [m for m in (series.get("momentum") or []) if m is not None]
    if mo:
        cur = mo[-1]
        out["momentum"] = ind(
            "momentum", N["momentum"], W["momentum"], cur, f"{cur * 100:+.1f}%",
            pct_score(mo, cur),
            f"trailing 12m real price change, {ordinal(pct_rank(mo, cur) * 100)} pct. "
            "Froth marker, not a sell signal - the classic top is momentum turning DOWN "
            "while valuation stays red",
            "Shiller CSV", series.get("price_date"),
        )
    else:
        gap("12-month momentum unavailable.")

    # ---- 6. HY credit spreads ---------------------------------------------
    hy_obs = fred.get("BAMLH0A0HYM2") or []
    hy_date, hy = latest_obs(hy_obs)
    wow = None
    if hy is not None:
        hy = hy * 100.0  # FRED reports OAS in percent; framework works in bps
        _, prior = latest_obs(hy_obs, dt.date.fromisoformat(hy_date) - dt.timedelta(days=7))
        if prior is not None:
            wow = hy - prior * 100.0
    if hy is None and fallback.get("BAMLH0A0HYM2") is not None:
        hy, hy_date = float(fallback["BAMLH0A0HYM2"]), fallback.get("as_of")
    if hy is None:
        hy, hy_stale = carried(prev, "hy_spread", today)
        hy_date = (prev or {}).get("date")
    else:
        hy_stale = 0
    if hy is not None:
        if wow is None:
            prev_hy = (prev or {}).get("indicators", {}).get("hy_spread", {}).get("value")
            wow = hy - prev_hy if prev_hy is not None else None
        move = (
            f"{wow:+.0f}bps vs a week ago ({'WIDENING' if wow > 0 else 'tightening'})"
            if wow is not None
            else "week-over-week change unavailable"
        )
        out["hy_spread"] = ind(
            "hy_spread", N["hy_spread"], W["hy_spread"], hy, f"{hy:.0f}bps",
            interp(ANCHORS["hy_spread"], hy),
            f"EARLY-WARNING TRIPWIRE. {move}. Tight = complacent; past 500bps alongside an "
            "equity drawdown is the staged-deployment trigger",
            "FRED BAMLH0A0HYM2", hy_date, hy_stale,
        )
    else:
        gap("HY OAS unavailable: the early-warning tripwire is DARK this week.")

    # ---- 7. Yield curve ----------------------------------------------------
    curve_obs = fred.get("T10Y2Y") or []
    c_date, curve = latest_obs(curve_obs)
    if curve is None and fallback.get("T10Y2Y") is not None:
        curve, c_date = float(fallback["T10Y2Y"]), fallback.get("as_of")
    inverted_recently = any(
        v < 0
        for d, v in curve_obs
        if dt.date.fromisoformat(d) >= today - dt.timedelta(days=365 * 3)
    )
    if not curve_obs:
        # No series to inspect: the post-inversion asterisk is worth ~0.7 of score, so take
        # the routine's explicit call, else the last report's memory of it.
        override = inputs.get("curve_post_inversion")
        inverted_recently = bool(
            override if override is not None
            else (prev or {}).get("meta", {}).get("inverted_recently")
        )
    if curve is None:
        curve, c_stale = carried(prev, "curve", today)
        c_date = (prev or {}).get("date")
        inverted_recently = bool((prev or {}).get("meta", {}).get("inverted_recently"))
    else:
        c_stale = 0
    if curve is not None:
        if curve < 0:
            score = interp(ANCHORS["curve_inv"], curve)
            note = "inverted - deep inversion scores ~4"
        elif inverted_recently:
            score = 3.0
            note = (
                "positive after a post-inversion re-steepening = 3.0 (*) - recessions "
                "historically arrive 12-18 months AFTER un-inversion, not during the inversion"
            )
        else:
            score = interp(ANCHORS["curve_steep"], curve)
            note = "positive with no inversion in the trailing 36 months - steep and healthy"
        out["curve"] = ind(
            "curve", N["curve"], W["curve"], curve, f"{curve:+.2f} pts",
            score, note, "FRED T10Y2Y", c_date, c_stale,
        )
        out["curve"]["inverted_recently"] = inverted_recently
    else:
        gap("Yield curve unavailable.")

    # ---- 8. Fiscal ---------------------------------------------------------
    fis = inputs.get("fiscal") or {}
    deficit = fis.get("deficit_pct_gdp")
    debt = fis.get("debt_to_gdp")
    f_src, f_asof, f_stale = fis.get("source", "web"), fis.get("as_of"), 0
    if deficit is None:
        d_date, d_val = latest_obs(fred.get("FYFSGDA188S"))
        if d_val is not None:
            deficit, f_src, f_asof = abs(d_val), "FRED FYFSGDA188S", d_date
    if debt is None:
        db_date, db_val = latest_obs(fred.get("GFDEGDQ188S"))
        if db_val is not None:
            debt = db_val
    if deficit is None:
        deficit, f_stale = carried(prev, "fiscal", today)
        f_src, f_asof = "carried from previous report", (prev or {}).get("date")
    if deficit is not None:
        note = "~6% deficits at full employment scores 4.5"
        if debt:
            note = f"federal debt/GDP {debt:.0f}%. " + note
        out["fiscal"] = ind(
            "fiscal", N["fiscal"], W["fiscal"], deficit, f"{deficit:.1f}% of GDP deficit",
            interp(ANCHORS["fiscal"], deficit), note, f_src, f_asof, f_stale,
        )
        out["fiscal"]["debt_to_gdp"] = debt
    else:
        gap("Fiscal trajectory unavailable.")

    return out


def composite_of(indicators: dict) -> tuple[float | None, float]:
    num = sum(i["weight"] * i["score"] for i in indicators.values() if i.get("score") is not None)
    den = sum(i["weight"] for i in indicators.values() if i.get("score") is not None)
    if den == 0:
        return None, 0.0
    return num / den, den


# ---------------------------------------------------------------------------
# sentiment
# ---------------------------------------------------------------------------


def update_sentiment(inputs, today, prev=None) -> tuple[dict, list[str], list[str]]:
    """Apply this week's updates and report what moved.

    The diff basis is the previous report's own snapshot when there is one, so
    re-running a week is idempotent: the rolling data/sentiment_current.json has already
    absorbed the changes, and diffing against it would silently show nothing moving.
    """
    cur_path = DATA / "sentiment_current.json"
    base_path = DATA / "sentiment_baseline.json"
    state = (prev or {}).get("sentiment_snapshot")
    if state:
        state = json.loads(json.dumps(state))  # never mutate the archived report
    else:
        state = load_json(cur_path, None) or load_json(base_path, None)
    if not state:
        gap("No sentiment state found (data/sentiment_baseline.json missing).")
        return {"roster": [], "fed": {}}, [], []
    changes: list[str] = []
    statement_only: list[str] = []
    by_name = {r["name"].lower(): r for r in state.get("roster", [])}
    for upd in inputs.get("sentiment_updates", []) or []:
        row = by_name.get(str(upd.get("name", "")).lower())
        if not row:
            gap(f"Sentiment update for unknown name '{upd.get('name')}' ignored "
                "(roster is defined in FRAMEWORK.md).")
            continue
        moved = []
        for f, label in (("s12", "12m"), ("s24", "24m"), ("s36", "36m")):
            if upd.get(f) is not None and upd[f] != row.get(f):
                moved.append(f"{label} {row.get(f):+d} -> {upd[f]:+d}")
                row[f] = upd[f]
        if upd.get("conviction") is not None and upd["conviction"] != row.get("conviction"):
            moved.append(f"conviction {row.get('conviction')} -> {upd['conviction']}")
            row["conviction"] = upd["conviction"]
        for f in ("note", "gap", "school"):
            if upd.get(f) is not None:
                row[f] = upd[f]
        if upd.get("statement"):
            st = upd["statement"]
            row["last_statement"] = st.get("date", today.isoformat())
            row["statement_summary"] = st.get("summary", "")
            row["statement_url"] = st.get("url", "")
            if not moved:
                row["changed_this_week"] = True
                statement_only.append(row["name"])
                continue
        if moved:
            row["changed_this_week"] = True
            changes.append(f"{row['name']}: " + "; ".join(moved))
        else:
            row["changed_this_week"] = False
    for r in state.get("roster", []):
        r.setdefault("changed_this_week", False)
    if inputs.get("fed"):
        fed_new = inputs["fed"]
        old = (state.get("fed") or {}).get("stance")
        state["fed"] = {**(state.get("fed") or {}), **fed_new, "as_of": fed_new.get("as_of", today.isoformat())}
        if old and fed_new.get("stance") and fed_new["stance"] != old:
            changes.append(f"Fed: {old} -> {fed_new['stance']}")
    state["as_of"] = today.isoformat()
    return state, changes, statement_only


def split_roster(roster: list[dict]) -> dict:
    bears = sorted([r for r in roster if r.get("s12", 0) <= -2], key=lambda r: r.get("s12", 0))
    bulls = sorted([r for r in roster if r.get("s12", 0) >= 2], key=lambda r: -r.get("s12", 0))
    middle = sorted(
        [r for r in roster if -2 < r.get("s12", 0) < 2], key=lambda r: -r.get("s12", 0)
    )
    return {"bears": bears, "middle": middle, "bulls": bulls}


# ---------------------------------------------------------------------------
# diffing
# ---------------------------------------------------------------------------


def trend_arrows(indicators, four_wk):
    old = (four_wk or {}).get("indicators", {})
    for key, i in indicators.items():
        o = old.get(key, {}).get("score")
        if o is None or i.get("score") is None:
            i["trend"] = "-"
            i["trend_delta"] = None
            continue
        d = i["score"] - o
        i["trend_delta"] = d
        i["trend"] = "^" if d > 0.1 else ("v" if d < -0.1 else "=")


def what_changed(indicators, prev, sentiment_changes, statement_only, composite):
    bullets = []
    old = (prev or {}).get("indicators", {})
    for key, name, _ in INDICATORS:
        i = indicators.get(key)
        if not i or i.get("score") is None:
            continue
        o = old.get(key, {}).get("score")
        if o is None:
            continue
        d = i["score"] - o
        if abs(d) > 0.2:
            bullets.append(
                f"{name}: score {o:.2f} -> {i['score']:.2f} ({d:+.2f}), now {i['display']}"
            )
    if prev and prev.get("composite") is not None and composite is not None:
        d = composite - prev["composite"]
        if abs(d) >= 0.01:
            bullets.insert(
                0, f"Composite {prev['composite']:.2f} -> {composite:.2f} ({d:+.2f})"
            )
    bullets.extend(sentiment_changes)
    if statement_only:
        bullets.append(
            "New statements, scores unchanged: " + ", ".join(sorted(statement_only))
        )
    if not bullets:
        bullets.append(
            "No indicator moved more than 0.2 and no roster member changed score or issued a "
            "new statement." if prev else "First report - no prior week to diff against."
        )
    return bullets[:12]


def dca_paragraph(composite, band_label, indicators):
    if composite is None:
        return (
            "The composite could not be computed this week, so no rule fires: base DCA "
            "continues unchanged. Treat this as a data outage, not a signal."
        )
    hy = indicators.get("hy_spread", {})
    hy_val = hy.get("value")
    lines = [
        f"Composite {composite:.2f} ({band_label}). Base DCA continues regardless of score."
    ]
    if composite < 2.5:
        lines.append("Below 2.5: deploy remaining dry powder aggressively - that rule is LIVE.")
    elif composite < 3.5:
        lines.append("Below 3.5: double the monthly DCA clip - that rule is LIVE.")
    else:
        lines.append(
            f"Above 3.5, so the DCA-doubling rule stays dormant; it arms at 3.5 "
            f"({composite - 3.5:+.2f} away). Hold dry powder."
        )
    if hy_val is not None:
        if hy_val > 500:
            lines.append(
                f"HY spreads at {hy_val:.0f}bps are past 500 - if equities are drawing down, "
                "begin staged deployment: a tranche at -10%, more at -15%/-20%, the bulk at "
                "-20%+ with spreads confirming."
            )
        else:
            lines.append(
                f"HY spreads at {hy_val:.0f}bps are still inside 500bps, so the staged "
                "dry-powder trigger is not armed. Credit is the tripwire: it goes first."
            )
    else:
        lines.append(
            "HY spreads were unavailable this week, so the credit tripwire is dark - do not "
            "read the quiet as calm."
        )
    return " ".join(lines)


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def render_markdown(ctx) -> str:
    L = []
    A = L.append
    A(f"# Froth Dashboard - {ctx['date']}")
    A("")
    comp = f"{ctx['composite']:.2f}" if ctx["composite"] is not None else "n/a"
    delta = f" ({ctx['composite_delta']:+.2f} vs {ctx['prev_date']})" if ctx["composite_delta"] is not None else ""
    A(f"**Composite {comp} - {ctx['band_label']}**{delta}")
    A("")
    if ctx["gaps"]:
        A(f"> Data gaps this run: {len(ctx['gaps'])} (see footer). Weights renormalised over "
          f"{ctx['weight_covered'] * 100:.0f}% of the framework.")
        A("")
    A("## Indicators")
    A("")
    A("| Indicator | Value | Score | 4wk | Note |")
    A("|---|---|---|---|---|")
    for i in ctx["indicators"]:
        score = f"{i['score']:.2f}" if i.get("score") is not None else "n/a"
        stale = f" _stale ({i['stale_weeks']}w)_" if i.get("stale_weeks") else ""
        A(f"| {i['name']} ({i['weight'] * 100:.0f}%) | {i['display']}{stale} | {score} | "
          f"{i.get('trend', '-')} | {i['note']} |")
    A("")
    A("## What changed")
    A("")
    for b in ctx["what_changed"]:
        A(f"- {b}")
    A("")
    A("## Sentiment snapshot")
    A("")
    for bucket, label in (("bears", "Bears"), ("middle", "Middle"), ("bulls", "Bulls")):
        A(f"**{label}**")
        A("")
        for r in ctx["sentiment"][bucket]:
            mark = " *(changed)*" if r.get("changed_this_week") else ""
            note = f" - {r['note']}" if r.get("note") else ""
            A(f"- {r['name']} {r['s12']:+d}/{r['s24']:+d}/{r['s36']:+d} c{r['conviction']} "
              f"[{r['school']}]{note}{mark}")
        A("")
    fed = ctx.get("fed") or {}
    if fed:
        A(f"**Fed / policy:** {fed.get('stance', 'n/a')} - {fed.get('read', '')}")
        A("")
    A("## Reading list")
    A("")
    if ctx["reading_list"]:
        for a in ctx["reading_list"]:
            src = f" ({a['source']}" + (f", {a['date']})" if a.get("date") else ")") if a.get("source") else ""
            A(f"- [{a['title']}]({a.get('url', '')}){src}  ")
            A(f"  *Why it matters:* {a.get('why', '')}")
    else:
        A("- No articles supplied this week.")
    A("")
    A("## For my DCA plan")
    A("")
    A(ctx["dca"])
    A("")
    A("**The rules, restated:**")
    A("")
    for r in DEPLOYMENT_RULES:
        A(f"- {r}")
    A("")
    A("---")
    A("")
    A(f"_{BACKTEST_LINE}_")
    A("")
    A("**Data vintage:** " + "; ".join(ctx["vintage"]))
    if ctx["gaps"]:
        A("")
        A("**Gaps flagged this run:**")
        A("")
        for g in ctx["gaps"]:
            A(f"- {g}")
    A("")
    A("_Reply-to-discuss: open Claude Code in the market_genius repo._")
    return "\n".join(L) + "\n"


def render_html(ctx) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template("report.html").render(**ctx, rules=DEPLOYMENT_RULES,
                                                  backtest=BACKTEST_LINE)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the weekly froth dashboard report.")
    ap.add_argument("--date", default=dt.date.today().isoformat(), help="report date (YYYY-MM-DD)")
    ap.add_argument("--offline", action="store_true", help="use cached data only, no network")
    ap.add_argument("--no-write", action="store_true", help="render but do not write files")
    ap.add_argument("--inputs", default=str(DATA / "weekly_inputs.json"),
                    help="supplemental research JSON written by the weekly routine")
    ap.add_argument("--preview", action="store_true",
                    help="render to out/preview.html only: no report archive, no sentiment write")
    args = ap.parse_args()

    today = dt.date.fromisoformat(args.date)
    print(f"Building froth dashboard for {today}...", file=sys.stderr)

    inputs_path = Path(args.inputs)
    inputs = load_json(inputs_path, {})
    if not inputs:
        gap(f"{inputs_path.name} missing or empty: no web-sourced values this run.")

    shiller_text, shiller_src, shiller_stale = fetch_shiller(today, args.offline)
    fred = {}
    for sid, years in (
        ("CPIAUCSL", 12),
        ("DGS10", 6),
        ("BAMLH0A0HYM2", 6),
        ("T10Y2Y", 6),
        ("GFDEGDQ188S", 6),
        ("FYFSGDA188S", 8),
    ):
        obs, _, _ = fetch_fred(sid, today, args.offline, years)
        fred[sid] = obs

    series = (build_series(parse_shiller(shiller_text), fred.get("CPIAUCSL"), inputs)
              if shiller_text else {})

    prev_all = previous_reports(today)
    prev = pick_prev(prev_all)
    four_wk = pick_four_weeks_ago(prev_all, today)

    indicators = score_indicators(series, fred, inputs, prev, today)
    trend_arrows(indicators, four_wk)
    composite, weight_covered = composite_of(indicators)
    band_label, band_color, band_name = band_for(composite) if composite is not None else ("no composite", "#666", "grey")

    sentiment_state, sentiment_changes, statement_only = update_sentiment(inputs, today, prev)
    buckets = split_roster(sentiment_state.get("roster", []))

    composite_delta = None
    if prev and prev.get("composite") is not None and composite is not None:
        composite_delta = composite - prev["composite"]

    vintage = [
        f"S&P {series.get('price_level'):,.0f} at {series.get('price_date', 'n/a')} "
        f"({series.get('price_source', 'n/a')})"
        if series.get("price_level") else "S&P price unavailable",
        shiller_src,
    ]
    if series.get("shiller_cpi_last"):
        vintage.append(f"Shiller CPI column ends {series['shiller_cpi_last']}")
    if series.get("cpi_extended_to"):
        vintage.append(f"CPI extended to {series['cpi_extended_to']} via FRED CPIAUCSL")
    if series.get("cpi_assumed_from"):
        vintage.append(f"CPI assumed forward from {series['cpi_assumed_from']}")
    if series.get("earnings_vintage"):
        vintage.append(f"10yr real earnings avg carried from {series['earnings_vintage']}")
    if inputs.get("research_date"):
        vintage.append(f"web research {inputs['research_date']}")

    ordered = [indicators[k] for k, _, _ in INDICATORS if k in indicators]

    ctx = {
        "date": today.isoformat(),
        "composite": composite,
        "composite_display": f"{composite:.2f}" if composite is not None else "n/a",
        "composite_delta": composite_delta,
        "band_label": band_label,
        "band_color": band_color,
        "band_name": band_name,
        "prev_date": (prev or {}).get("date"),
        "indicators": ordered,
        "what_changed": what_changed(indicators, prev, sentiment_changes,
                                     statement_only, composite),
        "sentiment": buckets,
        "fed": sentiment_state.get("fed", {}),
        "reading_list": inputs.get("reading_list", []) or [],
        "dca": dca_paragraph(composite, band_label, indicators),
        "vintage": vintage,
        "gaps": GAPS,
        "weight_covered": weight_covered,
    }

    md = render_markdown(ctx)
    try:
        html = render_html(ctx)
    except Exception as exc:  # noqa: BLE001 - a template break must not kill the report
        gap(f"HTML template failed ({exc.__class__.__name__}: {exc}); emailing the markdown instead.")
        html = "<pre style=\"font:14px/1.5 -apple-system,Segoe UI,sans-serif;white-space:pre-wrap\">" + \
               md.replace("&", "&amp;").replace("<", "&lt;") + "</pre>"

    if args.no_write:
        print(md)
        return 0

    if args.preview:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "preview.html").write_text(html)
        print(f"preview: composite={ctx['composite_display']} band={band_label} "
              f"indicators={len(indicators)}/8 gaps={len(GAPS)} -> out/preview.html", file=sys.stderr)
        return 0

    REPORTS.mkdir(parents=True, exist_ok=True)
    REPORT_DATA.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    (REPORTS / f"report-{today}.md").write_text(md)
    record = {
        "date": today.isoformat(),
        "composite": composite,
        "band": band_label,
        "weight_covered": weight_covered,
        "indicators": {
            k: {
                "value": v["value"], "score": v["score"], "display": v["display"],
                "as_of": v["as_of"], "stale_weeks": v["stale_weeks"], "source": v["source"],
            }
            for k, v in indicators.items()
        },
        "sentiment_changes": sentiment_changes,
        "new_statements": statement_only,
        "sentiment_snapshot": sentiment_state,
        "reading_list": ctx["reading_list"],
        "gaps": GAPS,
        "meta": {
            "inverted_recently": indicators.get("curve", {}).get("inverted_recently", False),
            "price_date": series.get("price_date"),
            "earnings_vintage": series.get("earnings_vintage"),
        },
    }
    (REPORT_DATA / f"report-{today}.json").write_text(json.dumps(record, indent=2))
    (DATA / "sentiment_current.json").write_text(json.dumps(sentiment_state, indent=2))
    (OUT / "email.html").write_text(html)
    (OUT / "meta.json").write_text(
        json.dumps(
            {
                "date": today.isoformat(),
                "composite": round(composite, 2) if composite is not None else None,
                "band": band_label,
                "subject": f"Froth Dashboard - {today} - Composite "
                           f"{composite:.2f} ({band_label})" if composite is not None
                           else f"Froth Dashboard - {today} - Composite unavailable (data gap)",
                "gaps": len(GAPS),
            },
            indent=2,
        )
    )

    print(
        f"composite={ctx['composite_display']} band={band_label} "
        f"indicators={len(indicators)}/8 gaps={len(GAPS)}",
        file=sys.stderr,
    )
    print(f"wrote reports/report-{today}.md and out/email.html", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - last-resort: never leave the routine without a signal
        traceback.print_exc()
        print("build_report.py failed hard; send_email.sh will report the failure.", file=sys.stderr)
        sys.exit(1)

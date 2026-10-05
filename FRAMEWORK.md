# FRAMEWORK.md — Froth Dashboard Methodology

**Single source of truth.** `scripts/build_report.py` reads its weights and scoring
anchors from this file's companion constants; the weekly routine reads the prose here to
decide what to research. Edit this file to evolve the framework — add an indicator, change
a weight, add or drop a sentiment voice — and the next run picks it up.

> If you change a **weight** or an **anchor table**, mirror the change in the `INDICATORS`
> block near the top of `scripts/build_report.py`. Everything else (roster membership,
> research instructions, narrative rules) is picked up from this file by the routine
> without touching code.

---

## 1. Indicators (8, weighted, each scored 1–5)

Higher score = frothier / more dangerous. Composite is the weighted average.

| # | Indicator | Weight | Source | Scoring |
|---|-----------|--------|--------|---------|
| 1 | Shiller CAPE | 20% | Shiller dataset CSV + web cross-check | Expanding-window historical percentile → `percentile*4+1` |
| 2 | Buffett Indicator (mkt cap / GDP) | 15% | Web search | Anchored to historical range |
| 3 | Excess CAPE Yield | 15% | Derived: `1/CAPE − real 10y` | Low / negative premium = high score |
| 4 | Price vs 10-year trend | 10% | Shiller CSV | Real price ÷ 120-month MA of real price, expanding percentile |
| 5 | 12-month momentum | 10% | Shiller CSV | Trailing 12-month real price change, expanding percentile |
| 6 | High-yield credit spreads | 10% | FRED `BAMLH0A0HYM2` | Tight = complacent = high score |
| 7 | Yield curve 10y−2y | 10% | FRED `T10Y2Y` | Inversion / post-inversion re-steepening logic below |
| 8 | Fiscal / debt trajectory | 10% | FRED `GFDEGDQ188S`, `FYFSGDA188S` + web | Deficit %GDP at full employment |

### 1.1 Shiller CAPE — 20%
Compute from the Shiller dataset
(`https://raw.githubusercontent.com/datasets/s-and-p-500/main/data/data.csv`);
cross-check the current value via web search (multpl.com). Score = expanding-window
historical percentile mapped to 1–5 (`percentile * 4 + 1`).

**Known data quirk:** the recent earnings and CPI fields in that CSV go stale (they stop
being populated years before the price column does). The pipeline handles this and
**flags the vintage in the report**:

- **CPI** is extended past the CSV with FRED `CPIAUCSL` (level-spliced at the overlap
  month). If FRED is unavailable it uses the CPI run-rate the routine supplies in
  `cpi_fallback.annual_rate_pct`; failing that it carries CPI flat and flags that real
  values now understate inflation, which biases CAPE and the percentiles **high**.
- **Earnings**: the last valid trailing-10-year real earnings average is carried forward
  (re-based to the current CPI base) and its vintage is printed.

Because a frozen earnings denominator against a rising price inflates CAPE, the **web
cross-check is the headline value when available**, and it is what gets scored — its
percentile is taken against the full historical CAPE distribution — with the computed
value shown next to it and the divergence called out when it exceeds 1.5 points. When the
cross-check is missing and earnings have been carried for more than 12 months, the report
labels the printed CAPE an **upper bound** rather than presenting it as a reading.

### 1.2 Buffett Indicator (market cap / GDP) — 15%
Web-search the current reading (currentmarketvaluation.com, gurufocus). Score against the
historical range: median ~85%, dotcom peak ~146–172%, current era 200%+ = 5.

Anchors (piecewise-linear, clamped 1–5): 50% → 1.0 · 85% → 3.0 · 120% → 4.0 ·
150% → 4.5 · 200% → 5.0.

### 1.3 Excess CAPE Yield — 15%
`ECY = 1/CAPE − real 10-year rate`, where the real 10y = 10-year nominal (FRED `DGS10`)
minus trailing 10-year CPI inflation computed from the Shiller CSV. A low or negative
equity risk premium = high score.

Anchors: +5.0% → 1.0 · +3.0% → 2.0 · +2.0% → 3.0 · +1.0% → 4.0 · 0.0% → 4.5 ·
−1.0% → 5.0.

### 1.4 Price vs 10-year trend — 10%
Real price ÷ 120-month moving average of real price, expanding percentile → 1–5.

### 1.5 12-month momentum — 10%
Trailing 12-month real price change, expanding percentile → 1–5.

**Always note in the report:** this is a froth marker, not a sell signal on its own. Watch
for **momentum turning DOWN while valuation stays red** — that combination is the classic
top signal.

The pipeline detects this automatically and raises a **TOP-SIGNAL WATCH** banner at the
top of the report whenever the momentum score has fallen more than 0.1 since the previous
report *and* the mean of the CAPE, Buffett and price-vs-trend scores is 4.0 or above. When
HY spreads widened over the same span, the banner says so too — credit confirming is what
separates a rotation from a turn. The banner matters more than the composite, which can
sit still while its components rotate underneath it.

### 1.6 High-yield credit spreads — 10%
FRED series `BAMLH0A0HYM2` (ICE BofA US High Yield OAS). Tight = complacent = high score.

Anchors: 200bps → 5.0 · 271bps → 4.5 · 500bps → 2.5 · 800bps → 1.0.

**This is the early-warning tripwire — always call out the week-over-week change**, even
when it is small, and say explicitly whether it is widening or tightening.

### 1.7 Yield curve 10y−2y — 10%
FRED series `T10Y2Y`.

- Currently inverted: −1.50 → 4.5 · −0.50 → 4.0 · 0.00 → 3.5 (deep inversion ≈ 4).
- Positive **after** an inversion in the trailing 36 months → **3.0 with an asterisk**:
  recessions historically arrive **12–18 months AFTER un-inversion**, not during the
  inversion. The asterisk text must appear in the report.
- Positive with no inversion in the trailing 36 months (steep and healthy):
  0.00 → 2.5 · +1.00 → 2.0 · +2.00 → 1.0.

### 1.8 Fiscal / debt trajectory — 10%
FRED `GFDEGDQ188S` (federal debt / GDP) and `FYFSGDA188S` (federal surplus/deficit as
%GDP); web-search for the current deficit as a share of GDP. ~6% deficits at full
employment = 4.5.

Anchors on deficit %GDP: 2% → 1.5 · 4% → 3.0 · 6% → 4.5 · 8% → 5.0. Debt/GDP is carried
in the note line for context, not scored separately.

---

## 2. Composite and bands

`COMPOSITE = Σ(weight × score) / Σ(weight of available indicators)` — weights are
renormalised over whatever scored successfully, and any dropped indicator is named in the
report.

| Composite | Band | Meaning |
|-----------|------|---------|
| < 2.5 | **deploy aggressively** | Cheap by this framework's standards |
| 2.5 – 3.5 | **DCA mode** | Normal |
| 3.5 – 4.5 | **frothy / hold dry powder** | Elevated |
| 4.5 + | **extreme** | Regime warning |

### Backtest context (include in every report footer, verbatim in substance)
At composite 4.5+, historically **40% probability a 20%+ real decline begins within 36
months, 29% for 30%+**; but 4.5+ zones also produced the longest melt-ups — **the signal
is regime, not timing**.

---

## 3. My deployment rules (repeat these in every email)

- **Base DCA continues regardless of score.**
- Composite drops **below 3.5** → **double the monthly DCA clip**.
- Composite **below 2.5** → **deploy remaining dry powder aggressively**.
- **HY spreads widening past 500bps alongside an equity drawdown** → begin staged
  dry-powder deployment: a tranche at **−10%** S&P drawdown, more at **−15% / −20%**, the
  bulk at **−20%+** with spreads confirming.

---

## 4. Sentiment roster (19 voices + the Fed)

Track **only statements, letters, filings, and interviews from the last 6 months**; newer
supersedes older. For each name record:

- direction score at **12 / 24 / 36 months**, each `−5 … +5` (negative = bearish)
- **conviction** 1–5
- **school**: strategist · macro · macro-trader · policy · credit · value · debt-cycle · bear
- **rhetoric-vs-positioning gap** — flag it whenever 13Fs, cash levels, or actual trades
  contradict the talk. Revealed preference beats quotes.

### Baseline as of 2026-08-21
Seeded into `data/sentiment_baseline.json` so week 1 has a diff basis.

| Name | 12/24/36 | Conv. | School | Note |
|------|----------|-------|--------|------|
| Yardeni | +4 / +4 / +4 | 5 | strategist | |
| Wood | +4 / +4 / +5 | 5 | strategist | |
| Lee | +4 / +3 / +3 | 5 | strategist | |
| Siegel | +2 / +2 / +2 | 3.5 | strategist | |
| Sonders | +1 / +1 / +1 | 3 | strategist | |
| Druckenmiller | +1 / +1 / 0 | 3 | macro-trader | |
| Roubini | +1 / +1 / +1 | 3 | policy | |
| Slok | +1 / 0 / 0 | 3 | policy | bull economy / bear market |
| El-Erian | 0 / 0 / +1 | 2 | policy | |
| Gross | 0 / 0 / 0 | 3 | macro | |
| Buffett–Berkshire | −1 / 0 / +1 | 4 | value | revealed pref: $365bn cash **but net buyer Q2-26** |
| Marks | −1 / −1 / 0 | 3 | credit | |
| Dimon | −2 / −2 / −1 | 3 | credit | rhetoric > action |
| Summers | −2 / −2 / −1 | 3 | policy | |
| Krugman | −2 / −2 / −1 | 3 | policy | |
| Dalio | −2 / −3 / −3 | 4 | debt-cycle | |
| Rosenberg | −2 / −3 / −2 | 4 | bear | |
| Grantham | −3 / −4 / −3 | 4 | value | |
| Burry | −4 / −4 / −2 | 5 | value | puts on NVDA / PLTR / SOXX |

**Fed / Warsh:** hold 3.50–3.75%, hike-bias dot plot — treat as a **policy headwind**.

**Excluded by my request:** Ackman, Icahn, and similar activist types. Do not add them.

---

## 5. Reading list rules

3–5 items, published in the **last two weeks** where possible. **Prioritise primary
sources** — investor memos, shareholder letters, 13F/13D filings, Fed transcripts and
speeches, earnings call transcripts — over journalism about them. Each item gets a
one-line *"why it matters to my framework"* tied to a specific indicator or roster name.

---

## 6. Report contract

Every report must contain, in order: composite band + delta · the top-signal banner when
it fires (§1.5) · indicator table with 4-week trend arrows · "what changed" diff ·
sentiment snapshot (bears / middle / bulls) · reading list · a "for my DCA plan" paragraph
that restates the deployment rules · footer with the backtest context line and
data-vintage notes.

The HY tile always states its **direction and the span it is measured over**, never just
the level — and when spreads are widening, the DCA paragraph says how far the 500bps
trigger is in units of the current weekly move.

**Degrade gracefully, never silently:** if a source fails, score what is available,
renormalise the weights, mark the tile *stale (n weeks)*, and say so in the email. A
report with a flagged gap always beats no report.

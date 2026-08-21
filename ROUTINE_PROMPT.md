# ROUTINE_PROMPT.md

The exact prompt to paste into `/schedule` when creating the weekly cloud routine.

**Schedule:** weekly, Monday 06:00 America/Los_Angeles
**Secrets the routine needs in its environment:** `FRED_API_KEY`, `RESEND_API_KEY`,
`REPORT_EMAIL_TO`

Copy everything inside the block.

---

```
Run the weekly froth dashboard for the market_genius repo.

Work the steps in order. Time-box the research to roughly 20 minutes: a report that goes
out with a flagged gap is always better than a report that does not go out. Never invent a
number - if you cannot source a value, leave the field out and let the pipeline flag it.

1. SETUP
   - Pull the latest repo state.
   - Read FRAMEWORK.md in full. It is the single source of truth: weights, scoring
     anchors, the roster, the reading-list rules and my deployment rules all live there,
     and I edit it between runs. If it disagrees with anything below, FRAMEWORK.md wins.
   - Read data/weekly_inputs.example.json for the exact shape and units of every field you
     are about to write, and reports/data/ for the most recent report, so you know what
     last week said.

2. RESEARCH — quantitative values
   Web-search each of these and note the source URL and the as-of date:
   - Current S&P 500 index level and its date -> spx_current. Do this one FIRST and never
     skip it: the Shiller dataset is monthly and lags by weeks, so without it the entire
     report is scored off last month's close.
   - Current Shiller CAPE (multpl.com preferred) -> cape_web
   - Buffett Indicator, total US market cap / GDP, in percent
     (currentmarketvaluation.com, gurufocus) -> buffett_indicator
   - Current federal deficit as a share of GDP, and debt/GDP if easy -> fiscal
   - Current CPI year-over-year rate -> cpi_fallback.annual_rate_pct (this is what keeps
     recent real prices from being dropped when FRED is unreachable)
   The FRED series (DGS10, T10Y2Y, BAMLH0A0HYM2, CPIAUCSL, GFDEGDQ188S, FYFSGDA188S) are
   fetched by the script using FRED_API_KEY. Only if that fetch fails should you
   web-search the 10-year Treasury yield, the 10y-2y spread and the ICE BofA high-yield
   OAS and put them in fred_fallback - watch the units noted in the example file, the HY
   OAS goes in as basis points. When you supply fred_fallback.T10Y2Y, also set
   curve_post_inversion (true if the curve has been inverted at any point in the trailing
   36 months) - it is worth about 0.7 of score.

3. RESEARCH — sentiment
   For each of the 19 names in FRAMEWORK.md, search for statements, letters, filings,
   interviews or 13F changes from the LAST TWO WEEKS. Skip the quiet ones entirely -
   omitting a name leaves last week's scores untouched, which is the correct behaviour.
   For any name with something new, write a sentiment_updates entry: revised 12/24/36-month
   direction scores (-5..+5), conviction 1-5, and a one-line statement summary with its
   URL and date. Newer supersedes older; only the last 6 months counts at all.
   Flag rhetoric-vs-positioning gaps - if a 13F, a cash level or an actual trade
   contradicts the talk, say so in the note and let revealed preference set the score.
   Also check the Fed's current stance and update the fed block if it moved.
   Do NOT add anyone to the roster. Ackman, Icahn and activist investors are excluded by
   request. An update for a name that is not on the roster is discarded by the script.

4. RESEARCH — reading list
   Find 3-5 items published in the last two weeks. Prioritise primary sources - investor
   memos, shareholder letters, 13F/13D filings, Fed transcripts and speeches, earnings
   call transcripts - over journalism about them. Each gets a one-line "why it matters"
   that names a specific indicator or roster member from my framework.

5. WRITE AND BUILD
   - Write everything you found to data/weekly_inputs.json, matching
     data/weekly_inputs.example.json. Omit fields you could not source; do not write
     placeholder numbers, and do not carry the seed values forward.
   - Run: python3 scripts/build_report.py
   - Read the gaps it printed. If a gap is something you can still fix by searching
     (a missing CAPE, a missing deficit figure), fix data/weekly_inputs.json and rerun
     once. Otherwise let the gap stand - it will be flagged in the email.

6. SEND
   - Run: bash scripts/send_email.sh
   - If the send fails, retry once, then report the failure in your run summary along
     with the Resend response body.

7. COMMIT
   - Commit reports/, data/sentiment_current.json and data/weekly_inputs.json with the
     message "weekly report {YYYY-MM-DD}" and push.

8. IF SOMETHING BREAKS
   - If build_report.py fails outright, fix the obvious thing once (bad JSON in
     weekly_inputs.json is the usual cause - validate it), then rerun.
   - If it still fails, send me an email anyway: run
     `python3 scripts/build_report.py --offline` to build from cache, and if even that
     fails, send a short plain-HTML note to REPORT_EMAIL_TO via the Resend API saying
     which step failed and what the error was. I would rather get a broken-pipeline
     notice than silence.
   - Never skip the email to "wait for better data".

9. SUMMARISE
   Finish with a short run summary: composite score and band, the delta vs last week, the
   HY spread and its week-over-week move, which roster members changed, and every gap that
   was flagged.
```

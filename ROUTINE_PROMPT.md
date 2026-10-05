# ROUTINE_PROMPT.md

The exact prompt to paste into `/schedule` when creating the weekly cloud routine.

**Schedule:** weekly, Monday 06:00 America/Los_Angeles
**Secrets the routine needs in its environment:** `FRED_API_KEY`, `RESEND_API_KEY`,
`REPORT_EMAIL_TO`

Copy everything inside the block.

---

```
Run the weekly froth dashboard for the market_genius repo.

Work in TWO PASSES. Pass one is small and must always finish: it gets a scored report
committed, pushed and emailed. Pass two is the expensive research sweep and enriches what
pass one already delivered. If you run short of time, context or anything else, you stop
in the middle of pass two having already shipped a report - never the other way round.

Never invent a number. If you cannot source a value, leave the field out and let the
pipeline flag it.

== SETUP ==
- Pull the latest repo state. Note the branch name; you will push back to it.
- Read FRAMEWORK.md in full. It is the single source of truth: weights, scoring anchors,
  the roster, the reading-list rules and my deployment rules all live there, and I edit it
  between runs. If it disagrees with anything below, FRAMEWORK.md wins.
- Read data/weekly_inputs.example.json for the exact shape and units of every field, and
  the newest file in reports/data/ so you know what last week said.

== PASS ONE: ship a report (do not skip, do not reorder) ==
1. Web-search these six values, noting source URL and as-of date for each:
   - Current S&P 500 index level and its date -> spx_current. FIRST, and never skipped:
     the Shiller dataset is monthly and lags by weeks, so without it the whole report is
     scored off last month's close.
   - Current Shiller CAPE (multpl.com preferred, gurufocus fine) -> cape_web
   - Buffett Indicator, total US market cap / GDP, percent -> buffett_indicator.
     Methodologies differ by 60+ points; keep using the same source family as last week's
     report so the week-over-week stays comparable, and note the alternative.
   - Current federal deficit as a share of GDP, plus debt/GDP if easy -> fiscal
   - Current CPI year-over-year rate -> cpi_fallback.annual_rate_pct
   - The FRED series (DGS10, T10Y2Y, BAMLH0A0HYM2) ONLY IF the API fetch fails or
     FRED_API_KEY is missing -> fred_fallback. Watch the units in the example file: the HY
     OAS goes in as BASIS POINTS. When you supply T10Y2Y, also set curve_post_inversion
     (true if the curve inverted at any point in the trailing 36 months) - it is worth
     about 0.7 of score.
2. Write what you have to data/weekly_inputs.json with sentiment_updates and reading_list
   left as empty arrays for now.
3. Run: python3 scripts/build_report.py
4. Commit reports/ and data/ with the message "weekly report {YYYY-MM-DD}" and push.
5. VERIFY THE PUSH LANDED: run `git log origin/<branch> -1 --oneline` and confirm your
   commit is there. A run that researched perfectly and pushed nothing is a failed run -
   this has happened before, for seven consecutive weeks, so check rather than assume.
6. Run: bash scripts/send_email.sh. If it fails, retry once, then carry on to pass two
   and report the failure with the Resend response body in your summary.

== PASS TWO: enrich ==
7. Sentiment. For each of the 19 names in FRAMEWORK.md, search for statements, letters,
   filings, interviews or 13F changes from the LAST TWO WEEKS. Skip the quiet ones
   entirely - omitting a name leaves last week's scores untouched, which is the correct
   behaviour, so a partial sweep is a valid outcome and not a reason to delay the report.
   For any name with something new, add a sentiment_updates entry: revised 12/24/36-month
   scores (-5..+5), conviction 1-5, and a one-line statement summary with URL and date.
   Newer supersedes older; only the last 6 months counts at all.
   Flag rhetoric-vs-positioning gaps - if a 13F, a cash level or an actual trade
   contradicts the talk, say so in the note and let revealed preference set the score.
   Also check the Fed's current stance and update the fed block if it moved.
   Do NOT add anyone to the roster. Ackman, Icahn and activist investors are excluded by
   request; an off-roster update is discarded by the script.
8. Reading list. 3-5 items published in the last two weeks. Prioritise primary sources -
   memos, shareholder letters, 13F/13D filings, Fed transcripts and speeches, earnings
   call transcripts - over journalism about them. Each gets a one-line "why it matters"
   naming a specific indicator or roster member.
9. Merge both into data/weekly_inputs.json, rerun build_report.py, re-send the email, then
   commit and push again with "weekly report {YYYY-MM-DD} (enriched)". Re-verify the push.

== IF SOMETHING BREAKS ==
- build_report.py failing outright is almost always bad JSON in weekly_inputs.json.
  Validate it with `python3 -c "import json;json.load(open('data/weekly_inputs.json'))"`,
  fix, rerun once.
- If it still fails, run `python3 scripts/build_report.py --offline` to build from cache.
- If even that fails, send me a short plain-HTML note via the Resend API saying which step
  failed and what the error was. I would rather get a broken-pipeline notice than silence.
- Never skip the email to "wait for better data". Never end a run without either a pushed
  commit or an explicit statement of what stopped you.

== SUMMARISE ==
Finish with: composite score and band, the delta vs last report, the HY spread and its
move since the last report, whether the FRAMEWORK 1.5 top-signal line fired, which roster
members changed, which names you did not get to, and every gap the build flagged.
```

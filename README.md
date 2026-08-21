# market_genius — weekly froth dashboard

A self-updating market-froth dashboard and sentiment report. Every Monday a cloud routine
refreshes eight quantitative indicators, re-scores a roster of 19 investors and
economists, picks 3–5 things worth reading, emails the result, and commits the report to
`reports/` so future Claude Code sessions can discuss the trend with full history.

```
FRAMEWORK.md              the methodology — weights, scoring anchors, roster, deployment
                          rules. Single source of truth. Edit this to evolve the system.
ROUTINE_PROMPT.md         the prompt to paste into /schedule for the weekly cloud routine.
scripts/build_report.py   fetches data, scores, renders the report and the email.
scripts/send_email.sh     sends out/email.html via Resend.
templates/report.html     the email body (inline CSS, no JS, email-client safe).
data/sentiment_baseline.json   frozen 2026-08-21 baseline — week 1's diff basis.
data/sentiment_current.json    rolling sentiment state, updated each run and committed.
data/weekly_inputs.json        what the routine researched this week (overwritten weekly).
data/weekly_inputs.example.json  every supported field, documented.
reports/report-YYYY-MM-DD.md   the archive you converse with.
reports/data/*.json            machine-readable archive, used for week-over-week diffs.
tests/smoke_test.py       offline check that the pipeline still scores and renders.
out/                      email.html + meta.json (gitignored, rebuilt every run).
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env      # then fill in the three values
```

| Secret | Where to get it |
|---|---|
| `FRED_API_KEY` | https://fred.stlouisfed.org/docs/api/api_key.html — free, instant |
| `RESEND_API_KEY` | https://resend.com → API Keys — free tier is 100 emails/day |
| `REPORT_EMAIL_TO` | your address; comma-separated list also works |

`.env` is gitignored. Never commit real keys. The cloud routine keeps its own copy of
these three in its environment configuration.

## Running it manually

```bash
make build      # build reports/report-$(date +%F).md and out/email.html
make preview    # render out/preview.html only, leaving the archive alone
make email      # send the already-built out/email.html
make report     # build + email
make offline    # build from cached data only, no network
make build DATE=2026-08-21   # any command takes DATE=
python3 tests/smoke_test.py  # offline pipeline check
```

`make build` only computes what it can reach. The web-sourced values (current CAPE
cross-check, Buffett indicator, deficit, sentiment statements, reading list) come from
`data/weekly_inputs.json`, which the **routine** writes after searching. Run manually
without refreshing that file and the report carries last week's web values forward and
marks them *stale (n weeks)*.

## How it degrades

Nothing in the pipeline throws away a report because a source is down. Each value is
resolved in order — **live API → local cache → routine-supplied fallback → last week's
value, carried forward and aged** — and only then dropped, with the framework weights
renormalised over whatever scored and every gap listed at the bottom of the email. A
report with a flagged hole always beats no report.

Three data quirks are handled explicitly, because the Shiller CSV is monthly and its
price column outlives its other columns by years:

- **Price** is monthly and lags by weeks. The routine supplies the current index level in
  `spx_current`, which replaces that month's close (or is appended as a new month, keeping
  the 12-month momentum lookback exactly twelve rows back). Without it the entire report
  is scored off last month's close.

- **CPI** stops in 2023 in that file. It is extended with FRED `CPIAUCSL`, or with the CPI
  run-rate the routine supplies, or carried flat with a loud warning that real values now
  understate inflation.
- **Earnings** stop even earlier. The trailing 10-year real earnings average is carried
  forward, so a computed CAPE drifts **high** over time. That is why the routine
  web-searches the current CAPE: when the cross-check is present it becomes the headline
  and the scored value; when it is absent, the report labels its own CAPE an upper bound.

## The weekly run

A cloud Routine is already scheduled:

| | |
|---|---|
| Name | Weekly froth dashboard — market_genius |
| Trigger id | `trig_01YDpjcbLYeN15i81Cm2Fy7w` |
| Schedule | `0 13 * * 1` UTC = **Monday 06:00 America/Los_Angeles** while PDT is in effect |
| Mode | fresh session per firing, in the Default environment |
| Notifications | push on, email off (the report itself arrives by email) |

Manage it at [claude.ai/code/routines](https://claude.ai/code/routines) — that is also
where run history and errors live if a Monday goes quiet.

**Two things it still needs from you:**

1. **Secrets.** Add `FRED_API_KEY`, `RESEND_API_KEY` and `REPORT_EMAIL_TO` to the
   routine's environment configuration. Until `RESEND_API_KEY` and `REPORT_EMAIL_TO`
   exist, the routine still researches, builds and commits the report — it just cannot
   email it, and says so in its run summary.
2. **A branch it can find.** The routine checks out
   `claude/market-dashboard-deploy-o2g3mc` if the default branch does not have the
   pipeline. Merge that branch and it will use the default branch instead.

Cron is evaluated in UTC, so when the US falls back to PST in November the run drifts to
05:00 local. Change the expression to `0 14 * * 1` then, or leave it — nothing depends on
the hour.

`ROUTINE_PROMPT.md` holds the instructions the routine follows; it reads that file fresh
on every firing, so editing it changes the routine's behaviour without touching the
trigger.

The routine reads `FRAMEWORK.md` fresh each week too. Both files are the control surface;
the trigger itself only points at them.

## Changing the framework

Edit `FRAMEWORK.md`. Roster membership, research instructions and narrative rules are read
by the routine straight from that file and need no code change. If you change a **weight**
or a **scoring anchor**, mirror it in the `INDICATORS` / `ANCHORS` blocks at the top of
`scripts/build_report.py`, then run `python3 tests/smoke_test.py`.

## Reruns are safe

Each report archives a full snapshot of the sentiment roster alongside its scores. Diffs
are computed against the **previous report's snapshot**, not against the rolling
`data/sentiment_current.json`, so re-running a week does not silently flatten "what
changed" — the second run produces the same diff as the first.

## Talking to the archive

Open Claude Code in this repo and ask, e.g.:

- "diff this week vs four weeks ago — what moved and why?"
- "when did the composite last cross 4.5, and what did HY spreads do around it?"
- "which roster members have changed their 12-month score at least twice this year?"

Everything it needs is in `reports/` and `reports/data/`.

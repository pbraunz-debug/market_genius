"""Publish one run of the froth dashboard to anotherroof.com.

    python3 scripts/publish_site.py --site /path/to/anotherroof [--date YYYY-MM-DD]

Copies into the anotherroof repo:
    reports/web/report-DATE.html  ->  public/projects/froth/DATE/index.html
    reports/data/report-DATE.json ->  src/data/froth/DATE.json   (numbers only, for the history charts)
and screenshots the page to public/projects/froth/DATE/thumb.jpg when Playwright is available.
The caller commits and pushes the anotherroof repo; Vercel deploys it.
"""
import argparse
import datetime as dt
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEYS = ["cape", "buffett", "ecy", "trend", "momentum", "hy_spread", "curve", "fiscal"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True, help="path to a clone of the anotherroof repo")
    ap.add_argument("--date", help="report date (default: newest report)")
    args = ap.parse_args()

    site = Path(args.site).resolve()
    if not (site / "astro.config.mjs").exists():
        print(f"publish_site: {site} does not look like the anotherroof repo", file=sys.stderr)
        return 2

    if args.date:
        date = args.date
    else:
        dated = sorted(p.stem.replace("report-", "") for p in (ROOT / "reports" / "web").glob("report-*.html"))
        if not dated:
            print("publish_site: no reports/web/report-*.html to publish", file=sys.stderr)
            return 2
        date = dated[-1]
    dt.date.fromisoformat(date)

    web = ROOT / "reports" / "web" / f"report-{date}.html"
    data = ROOT / "reports" / "data" / f"report-{date}.json"
    for f in (web, data):
        if not f.exists():
            print(f"publish_site: missing {f.relative_to(ROOT)}", file=sys.stderr)
            return 2

    run_dir = site / "public" / "projects" / "froth" / date
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "index.html").write_text(web.read_text())

    d = json.loads(data.read_text())
    condensed = {
        "date": d["date"],
        "composite": round(d["composite"], 3) if d.get("composite") is not None else None,
        "band": d.get("band"),
        "source": "pipeline",
        "indicators": {
            k: {"display": v["display"], "score": round(v["score"], 3), "value": v["value"]}
            for k, v in d.get("indicators", {}).items()
            if k in KEYS and v.get("score") is not None
        },
        # the roster positions, for the animated prognosticator quadrant
        "roster": [
            {"name": r["name"], "s12": r.get("s12"), "s24": r.get("s24"), "s36": r.get("s36"),
             "conviction": r.get("conviction"), "changed": bool(r.get("changed_this_week"))}
            for r in (d.get("sentiment_snapshot") or {}).get("roster", [])
        ],
    }
    if condensed["composite"] is None:
        print("publish_site: report has no composite; publishing the page but not the data point", file=sys.stderr)
    else:
        out = site / "src" / "data" / "froth"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{date}.json").write_text(json.dumps(condensed, indent=1))

    # the animated prognosticator quadrant on the froth entry page: rebuilt from every run
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from quadrant import write_player
        write_player(site)
    except Exception as exc:  # noqa: BLE001 - a missing chart must not block publishing
        print(f"publish_site: quadrant player skipped ({exc.__class__.__name__}: {exc})", file=sys.stderr)

    try:
        from playwright.sync_api import sync_playwright
        from PIL import Image

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto((run_dir / "index.html").as_uri())
            page.wait_for_timeout(800)
            page.add_style_tag(content=".sitebar{display:none!important}")
            png = page.screenshot(clip={"x": 0, "y": 0, "width": 1280, "height": 800})
            browser.close()
        Image.open(io.BytesIO(png)).convert("RGB").resize((640, 400), Image.LANCZOS).save(
            run_dir / "thumb.jpg", quality=80, optimize=True, progressive=True)
        thumb = "with thumbnail"
    except Exception as exc:  # noqa: BLE001 - a missing screenshot must not block publishing
        thumb = f"no thumbnail ({exc.__class__.__name__}: {exc})"

    print(f"published froth run {date} to {site.name}: page, data, {thumb}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

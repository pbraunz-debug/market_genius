#!/usr/bin/env python3
"""
Smoke test for the froth dashboard pipeline. No network required.

    python3 tests/smoke_test.py

Runs build_report.py twice against a synthetic inputs fixture - once with every input
present (full coverage) and once with none (worst-case degradation) - and asserts the
pipeline produces a scored, renderable report either way and never crashes. Run this
after editing FRAMEWORK.md weights or the scoring code.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "build_report.py"
FIXTURE = ROOT / "tests" / "fixtures" / "weekly_inputs.fixture.json"
FAILURES = []


def check(cond, label):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        FAILURES.append(label)


def run(inputs_path, label):
    print(f"\n== {label} ==")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--date", "2026-08-21", "--offline",
         "--inputs", str(inputs_path), "--preview"],
        capture_output=True, text=True, cwd=ROOT,
    )
    check(proc.returncode == 0, "exit code 0")
    html = (ROOT / "out" / "preview.html").read_text()
    return proc.stderr, html


def main():
    if not (ROOT / "data" / "cache" / "shiller.csv").exists():
        print("no cached Shiller data yet - run `make build` once online first", file=sys.stderr)
        return 2

    err, html = run(FIXTURE, "full coverage (synthetic fixture)")
    check("indicators=8/8" in err, "all 8 indicators scored")
    for section in ("Indicators", "What changed", "Sentiment snapshot", "Reading list",
                    "For my DCA plan", "Data vintage", "Reply-to-discuss"):
        check(section in html, f"email contains '{section}'")
    check("Dalio" in html and "SYNTHETIC test statement" in html, "sentiment update rendered")
    check("unknown name" in err, "off-roster sentiment update rejected, not silently applied")
    check("<script" not in html.lower(), "no JS in the email body")
    check("38.5" in html, "web CAPE cross-check is the headline value")

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump({}, fh)
        empty = fh.name
    err, html = run(empty, "worst case: empty inputs, offline, no FRED cache")
    check("composite=" in err and "band=" in err, "still produced a composite")
    check("gaps=" in err and "gaps=0" not in err, "gaps were flagged, not swallowed")
    check("Gaps flagged this run" in html, "gaps surfaced in the email itself")

    print(f"\n{len(FAILURES)} failure(s)" if FAILURES else "\nall checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

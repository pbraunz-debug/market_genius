#!/usr/bin/env bash
# Send out/email.html via the Resend API.
#
#   RESEND_API_KEY    required
#   REPORT_EMAIL_TO   required (comma-separated addresses allowed)
#   REPORT_EMAIL_FROM optional, defaults to Resend's onboarding sender
#
# Subject: "Froth Dashboard - {date} - Composite {score} ({band})", read from out/meta.json.
# Exits non-zero on a send failure so the routine can report it, but never edits the report.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HTML="$ROOT/out/email.html"
META="$ROOT/out/meta.json"
FROM="${REPORT_EMAIL_FROM:-Froth Dashboard <onboarding@resend.dev>}"

die() { echo "send_email.sh: $*" >&2; exit 1; }

[[ -f "$HTML" ]] || die "missing $HTML - run scripts/build_report.py first"
[[ -n "${RESEND_API_KEY:-}" ]] || die "RESEND_API_KEY is not set"
[[ -n "${REPORT_EMAIL_TO:-}" ]] || die "REPORT_EMAIL_TO is not set"

if [[ -f "$META" ]]; then
  SUBJECT="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["subject"])' "$META")"
else
  SUBJECT="Froth Dashboard - $(date +%F)"
fi

PAYLOAD="$(python3 - "$FROM" "$REPORT_EMAIL_TO" "$SUBJECT" "$HTML" <<'PY'
import json, sys
frm, to, subject, html_path = sys.argv[1:5]
print(json.dumps({
    "from": frm,
    "to": [a.strip() for a in to.split(",") if a.strip()],
    "subject": subject,
    "html": open(html_path).read(),
}))
PY
)"

echo "send_email.sh: sending \"$SUBJECT\" to $REPORT_EMAIL_TO" >&2

HTTP_CODE="$(curl -sS -o /tmp/resend_response.json -w '%{http_code}' \
  -X POST https://api.resend.com/emails \
  -H "Authorization: Bearer ${RESEND_API_KEY}" \
  -H "Content-Type: application/json" \
  --data-binary "$PAYLOAD")"

if [[ "$HTTP_CODE" =~ ^2 ]]; then
  echo "send_email.sh: sent (HTTP $HTTP_CODE) id=$(python3 -c 'import json;print(json.load(open("/tmp/resend_response.json")).get("id",""))' 2>/dev/null)" >&2
else
  echo "send_email.sh: FAILED (HTTP $HTTP_CODE)" >&2
  cat /tmp/resend_response.json >&2 || true
  echo >&2
  die "Resend rejected the send - check the Resend dashboard logs (auth or unverified sending domain are the usual causes)"
fi

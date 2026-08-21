SHELL := /bin/bash
PY ?= python3
DATE ?= $(shell date +%F)

.PHONY: help report build email preview offline install clean

help:
	@echo "make install   - pip install -r requirements.txt"
	@echo "make report    - full pipeline: build + send email (needs RESEND_API_KEY, REPORT_EMAIL_TO)"
	@echo "make build     - build reports/report-\$$(DATE).md and out/email.html only"
	@echo "make preview   - render out/preview.html without touching the archive"
	@echo "make offline   - build from cached data only, no network"
	@echo "make email     - send the already-built out/email.html"
	@echo "make clean     - remove out/"
	@echo ""
	@echo "Override the date with: make build DATE=2026-08-21"

install:
	$(PY) -m pip install -r requirements.txt

build:
	@set -a; [ -f .env ] && . ./.env; set +a; $(PY) scripts/build_report.py --date $(DATE)

preview:
	@set -a; [ -f .env ] && . ./.env; set +a; $(PY) scripts/build_report.py --date $(DATE) --preview

offline:
	@set -a; [ -f .env ] && . ./.env; set +a; $(PY) scripts/build_report.py --date $(DATE) --offline

email:
	@set -a; [ -f .env ] && . ./.env; set +a; bash scripts/send_email.sh

report: build email

clean:
	rm -rf out

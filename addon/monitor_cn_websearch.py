#!/usr/bin/env python3
"""Poll the Chinese AV web-search job: print progress every 60s, exit when the
background shell status file shows completed/failed/cancelled."""

import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "debrid-app", "data", "titles.sqlite")
CODES = os.path.join(ROOT, "addon", ".chinese_websearch_codes.json")
STATUS = sys.argv[1] if len(sys.argv) > 1 else ""
INTERVAL = int(os.environ.get("CN_POLL_INTERVAL", "60"))


def counts():
    db = sqlite3.connect(DB)
    updated = db.execute(
        "SELECT COUNT(*) FROM titles WHERE id LIKE 'cn:%' AND description LIKE '%Source: web search%'"
    ).fetchone()[0]
    db.close()
    return updated


def remaining():
    if os.path.exists(CODES):
        with open(CODES, encoding="utf-8") as f:
            return len(json.load(f))
    return -1


while True:
    updated = counts()
    rem = remaining()
    job = ""
    if os.path.exists(STATUS):
        with open(STATUS, encoding="utf-8") as f:
            st = json.load(f)
        job = st.get("status", "?")
    print(f"cn websearch updated={updated} remaining_codes={rem} job={job}", flush=True)
    if job in ("completed", "failed", "cancelled"):
        print("JOB DONE", flush=True)
        break
    time.sleep(INTERVAL)

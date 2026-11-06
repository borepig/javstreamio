#!/usr/bin/env python3
"""Poll the hnt enrichment job: print progress every 60s, exit when the
background shell status file shows completed/failed."""

import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "debrid-app", "data", "titles.sqlite")
STATUS = sys.argv[1] if len(sys.argv) > 1 else ""
INTERVAL = int(os.environ.get("ENRICH_POLL_INTERVAL", "60"))


def counts():
    db = sqlite3.connect(DB)
    enriched = db.execute(
        "SELECT COUNT(*) FROM titles WHERE id LIKE 'hnt:%' AND description NOT LIKE '%Source: Nyaa/Sukebei%'"
    ).fetchone()[0]
    streams = db.execute(
        "SELECT COUNT(*) FROM titles WHERE id LIKE 'hnt:%' AND stream_url != ''"
    ).fetchone()[0]
    db.close()
    return enriched, streams


while True:
    enriched, streams = counts()
    job = ""
    if os.path.exists(STATUS):
        with open(STATUS, encoding="utf-8") as f:
            st = json.load(f)
        job = st.get("status", "?")
    print(f"hnt enriched={enriched} stream_url={streams} job={job}", flush=True)
    if job in ("completed", "failed", "cancelled"):
        print("JOB DONE", flush=True)
        break
    time.sleep(INTERVAL)

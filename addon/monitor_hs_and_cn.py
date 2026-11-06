#!/usr/bin/env python3
"""Poll the Hentaistream catalog build and the CN web-search job; log progress
every 60s until both settle (catalog process exits + websearch process exits)."""

import json
import os
import sqlite3
import subprocess
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(ROOT, "addon", ".hentaistream_catalog.json")
DB = os.path.join(ROOT, "debrid-app", "data", "titles.sqlite")
INTERVAL = int(os.environ.get("MONITOR_INTERVAL", "60"))
MAX_POLLS = int(os.environ.get("MONITOR_MAX_POLLS", "90"))


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def catalog_stats():
    if not os.path.exists(CATALOG):
        return None
    try:
        c = json.load(open(CATALOG, encoding="utf-8"))
        return len(c), sum(1 for x in c if x.get("mp4"))
    except Exception:
        return None


def ps_grep(needle):
    out = subprocess.run(["ps", "-eo", "pid,cmd"], capture_output=True, text=True).stdout
    return any(needle in line for line in out.splitlines())


for i in range(1, MAX_POLLS + 1):
    parts = []
    cs = catalog_stats()
    if cs:
        parts.append(f"catalog={cs[0]} entries ({cs[1]} with mp4)")
    else:
        parts.append("catalog=building (no entries yet)")
    hs_running = ps_grep("build_hentaistream_catalog.py")
    parts.append(f"catalog_build={'running' if hs_running else 'done'}")
    db = sqlite3.connect(DB)
    cn_updated = db.execute(
        "SELECT COUNT(*) FROM titles WHERE id LIKE 'cn:%' AND description LIKE '%Source: web search%'"
    ).fetchone()[0]
    db.close()
    cn_running = ps_grep("websearch_chinese.py")
    parts.append(f"cn_updated={cn_updated} cn_job={'running' if cn_running else 'done'}")
    print(f"poll {i}: " + " | ".join(parts), flush=True)
    if not hs_running and not cn_running and cs is not None:
        print("BOTH JOBS SETTLED", flush=True)
        break
    time.sleep(INTERVAL)

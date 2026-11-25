#!/usr/bin/env python3
"""One progress check for a running --enrich-sqlite JavDB job.

Usage:
  python3 addon/monitor_javdb_enrich.py

Recounts filled genres in the live SQLite, keeps a small JSON state file to
compute rate + ETA across runs, and scans the run log for 403/429 backoff
streaks. Prints a single-line progress record to stdout.
"""
import json
import os
import sqlite3
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "debrid-app", "data", "titles.sqlite")
LOG_PATH = os.path.join(ROOT, ".javdb_enrich_run.log")
STATE_PATH = os.path.join(ROOT, ".javdb_enrich_progress.json")


JAV_CATS = ("jav-censored", "jav-uncensored")


def count_with_genres():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    where = "category IN ('jav-censored','jav-uncensored')"
    total = cur.execute(
        "SELECT COUNT(*) c FROM titles WHERE " + where
    ).fetchone()["c"]
    filled = cur.execute(
        "SELECT COUNT(*) c FROM titles WHERE " + where +
        " AND genres_json IS NOT NULL AND genres_json != '[]' AND genres_json != ''"
    ).fetchone()["c"]
    con.close()
    return total, filled


def log_errors():
    if not os.path.exists(LOG_PATH):
        return 0, 0
    n403 = n429 = 0
    with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if "403" in line:
                n403 += 1
            if "429" in line:
                n429 += 1
    return n403, n429


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"first_ts": None, "first_filled": None}


def save_state(st):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)


def main():
    total, filled = count_with_genres()
    missing = total - filled
    n403, n429 = log_errors()
    st = load_state()
    now = time.time()
    if st.get("first_ts") is None:
        st["first_ts"] = now
        st["first_filled"] = filled
    first_ts = st["first_ts"]
    first_filled = st["first_filled"]
    elapsed = now - first_ts
    delta = filled - first_filled
    rate = (delta / elapsed * 60.0) if elapsed > 0 else 0.0  # per-minute
    eta_min = (missing / rate) if rate > 0 else float("inf")
    flag = ("▲ with_genres increased by %d since monitor start" % delta) if filled > first_filled \
        else "no with_genres increase yet"
    save_state(st)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    eta_str = f"{eta_min:.0f} min" if eta_min != float("inf") else "n/a"
    print(
        f"[{ts}] javdb-enrich: filled={filled}/{total} (missing={missing}) | "
        f"rate={rate:.2f}/min | ETA={eta_str} | 403={n403} 429={n429} | {flag}"
    )


if __name__ == "__main__":
    main()

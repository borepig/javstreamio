#!/usr/bin/env python3
"""Robust 30-minute coverage monitor for the hnt enrichment job.

Replaces the fragile `nohup bash -c` monitor loop (which kept exiting).
This script runs in the background and appends a coverage line to
`.enrich_hentai_monitor.log` every 30 minutes while the enrichment
python is alive.
"""
import glob
import os
import sqlite3
import time

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJ, "debrid-app", "data", "titles.sqlite")
LOG = os.path.join(PROJ, ".enrich_hentai_monitor.log")
INTERVAL = 1800  # 30 minutes


def find_job_pid():
    """Find the running enrich_hentai.py python process."""
    import re
    import subprocess
    out = subprocess.check_output(["pgrep", "-af", "enrich_hentai.py"], text=True)
    pids = []
    for line in out.splitlines():
        # line: "<pid> python3 -u addon/enrich_hentai.py"
        parts = line.split(" ", 1)
        if len(parts) == 2 and "python3" in parts[1] and "enrich_hentai.py" in parts[1]:
            pids.append(parts[0])
    return pids[0] if pids else None


def alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (ProcessLookupError, ValueError):
        return False


def coverage_line():
    db = sqlite3.connect(DB)
    c = db.cursor()
    t = c.execute("SELECT COUNT(*) FROM titles WHERE id LIKE 'hnt:%'").fetchone()[0]
    s = c.execute(
        "SELECT COUNT(*) FROM titles WHERE id LIKE 'hnt:%' AND COALESCE(stream_url,'')<>''"
    ).fetchone()[0]
    p = c.execute(
        "SELECT COUNT(*) FROM titles WHERE id LIKE 'hnt:%' AND COALESCE(poster,'')<>''"
    ).fetchone()[0]
    db.close()
    ts = time.strftime("%m-%d %H:%M")
    return f"[{ts}] hnt total={t} stream_url={s}/{t} ({100*s//max(t,1)}%) poster={p}/{t} ({100*p//max(t,1)}%)"


def main():
    with open(LOG, "a") as f:
        pid = find_job_pid()
        while alive(pid):
            f.write(coverage_line() + "\n")
            f.flush()
            time.sleep(INTERVAL)
            # re-resolve the job pid (in case it was relaunched)
            new_pid = find_job_pid()
            if new_pid and new_pid != pid:
                pid = new_pid
        with open(LOG, "a") as f:
            f.write(f"[{time.strftime('%m-%d %H:%M')}] job finished\n")


if __name__ == "__main__":
    main()

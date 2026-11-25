#!/usr/bin/env python3
"""Sweep every dmm_missing=1 row: probe its fourhoi cover (browser UA, HEAD).

- cover 200 -> ensure fourhoi_missing=0 (resume-safe reset)
- cover 404/non-200 -> flip fourhoi_missing=1 so applyPublicArt falls
  through to the /poster proxy (forum poster) instead of sending a 404 URL.

Also reports the subset of 404 rows that lack forum_poster: those need a
JavDB cover stored in the DB (jdbstatic.com, allowlisted).
"""
import concurrent.futures
import json
import re
import sqlite3
import sys
import urllib.error
import urllib.request

DB = "debrid-app/data/titles.sqlite"
WORKERS = 16
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def fourhoi_url(title_id):
    raw = re.sub(r"^jav:", "", title_id, flags=re.I).strip()
    m = re.match(r"^([A-Za-z]{2,})[-_](\d+)$", raw)
    if not m:
        return ""
    return f"https://fourhoi.com/{m.group(1).lower()}-{m.group(2)}/cover-n.jpg"


def probe(url):
    if not url:
        return 0
    try:
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", UA)
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.getcode()
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def main():
    db = sqlite3.connect(DB)
    cur = db.cursor()
    rows = cur.execute(
        "SELECT id, forum_poster, fourhoi_missing FROM titles WHERE dmm_missing=1"
    ).fetchall()

    status = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(probe, fourhoi_url(r[0])): r for r in rows}
        for fut in concurrent.futures.as_completed(futs):
            r = futs[fut]
            status[r[0]] = fut.result()

    flip_to_1 = [r[0] for r in rows if status[r[0]] != 200 and r[2] != 1]
    flip_to_0 = [r[0] for r in rows if status[r[0]] == 200 and r[2] == 1]
    for tid in flip_to_1:
        cur.execute("UPDATE titles SET fourhoi_missing = 1 WHERE id = ?", (tid,))
    for tid in flip_to_0:
        cur.execute("UPDATE titles SET fourhoi_missing = 0 WHERE id = ?", (tid,))
    db.commit()

    no_fp_404 = [
        r[0] for r in rows
        if status[r[0]] != 200 and not (r[1] or "").strip()
    ]

    ok = sum(1 for r in rows if status[r[0]] == 200)
    print(f"probed {len(rows)} dmm_missing rows; fourhoi 200: {ok}, non-200: {len(rows) - ok}")
    print(f"flipped fourhoi_missing 0->1: {len(flip_to_1)}")
    print(f"reset fourhoi_missing 1->0: {len(flip_to_0)}")
    print(f"non-200 AND no forum_poster (need JavDB cover): {len(no_fp_404)}")
    for tid in no_fp_404[:20]:
        print("  ", tid)
    if len(no_fp_404) > 20:
        print(f"  ...and {len(no_fp_404) - 20} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())

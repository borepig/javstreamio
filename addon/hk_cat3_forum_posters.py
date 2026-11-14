#!/usr/bin/env python3
"""Backfill forum_poster for hk:cat3-* rows from data/forum-107-all.jsonl.

The forum-107 scrape stored each thread's preview image in the jsonl,
but only djhdhs.us covers made it into the DB at scrape time. This job
copies the first https preview image of every thread into the
forum_poster column (resume-safe: rows that already have a forum_poster
are skipped). The debrid-app's /poster proxy fetches these server-side,
so Stremio never hotlinks the random image CDNs.

Usage:
    python3 addon/hk_cat3_forum_posters.py
"""

import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "debrid-app" / "data" / "titles.sqlite"
JSONL = ROOT / "data" / "forum-107-all.jsonl"
THREAD_RE = re.compile(r"thread-(\d+)")


def first_https(images) -> str:
    for img in images or []:
        if isinstance(img, str) and img.startswith("https://"):
            return img
    return ""


def main() -> int:
    cover_by_thread = {}
    with JSONL.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            m = THREAD_RE.search(rec.get("url") or "")
            if m:
                cover_by_thread.setdefault(m.group(1), first_https(rec.get("preview_images")))

    # Open with a busy timeout so this coexists with the running genre-filler.
    db = sqlite3.connect(str(DB), timeout=30)
    rows = db.execute(
        "SELECT id, forum_poster FROM titles WHERE id LIKE 'hk:cat3-%'"
    ).fetchall()

    updated = skipped = missing = 0
    for row_id, forum_poster in rows:
        if forum_poster:
            skipped += 1
            continue
        thread = row_id.split("cat3-", 1)[1]
        cover = cover_by_thread.get(thread, "")
        if not cover:
            missing += 1
            continue
        db.execute("UPDATE titles SET forum_poster = ? WHERE id = ?", (cover, row_id))
        updated += 1

    db.commit()
    db.close()
    print(f"forum-poster backfill: updated={updated} skipped={skipped} missing_in_jsonl={missing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

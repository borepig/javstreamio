"""Rewrite the `poster` column of Chinese AV rows to GitHub raw URLs.

Reads `posters/manifest.json` (title_id -> cover code) and sets each row's
`poster` to `https://raw.githubusercontent.com/borepig/javstreamio/main/posters/<code>.jpg`
so Stremio fetches the poster directly from GitHub raw (zero Vercel image
traffic) instead of hotlink-protected iJavTorrent.

Usage (from repo root):
    python3 addon/apply_poster_urls.py
"""

import json
import os
import sqlite3

from build_addon import TITLES_SQLITE

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(REPO, "posters", "manifest.json")
RAW_BASE = "https://raw.githubusercontent.com/borepig/javstreamio/main/posters"


def main() -> None:
    with open(MANIFEST, encoding="utf-8") as f:
        manifest = json.load(f)
    conn = sqlite3.connect(TITLES_SQLITE)
    cur = conn.cursor()
    updated = 0
    for title_id, code in manifest.items():
        cur.execute(
            "UPDATE titles SET poster = ? WHERE id = ?",
            (f"{RAW_BASE}/{code}.jpg", title_id),
        )
        updated += cur.rowcount
    conn.commit()
    conn.close()
    print(f"apply_poster_urls: rewrote {updated} poster URLs to GitHub raw")


if __name__ == "__main__":
    main()

"""Download all iJavTorrent Chinese AV posters and stage them in the repo's
`posters/` folder for GitHub-raw hosting.

For every `chinese` title whose `poster` points at `images.ijavtorrent.com`,
extract the cover code from the URL, download the jpg (with the required
`Referer: https://www.ijavtorrent.com/`), and save it to `<repo>/posters/<code>.jpg`.

A manifest `posters/manifest.json` (id -> code) is written so the DB `poster`
column can be updated to the raw.githubusercontent.com URL.

Usage (from repo root):
    python3 addon/host_posters.py [--limit N]

Set JAV_PIC_REFEREE via env if the host changes.
"""

import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request

from build_addon import TITLES_SQLITE

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POSTERS_DIR = os.path.join(REPO, "posters")
MANIFEST = os.path.join(POSTERS_DIR, "manifest.json")
REFERER = "https://www.ijavtorrent.com/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")


def code_from_poster_url(url: str) -> str:
    """Extract the cover code (last path segment without extension)."""
    base = url.rstrip("/").split("/")[-1]
    return re.sub(r"\.[a-z0-9]+$", "", base)


def download(url: str, dest: str) -> bool:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Referer": REFERER}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            with open(dest, "wb") as f:
                f.write(resp.read())
        return os.path.getsize(dest) > 0
    except Exception as e:
        print(f"  download failed {url}: {e}")
        return False


def main() -> None:
    limit = 0
    if "--limit" in sys.argv:
        i = sys.argv.index("--limit")
        limit = int(sys.argv[i + 1])

    os.makedirs(POSTERS_DIR, exist_ok=True)
    conn = sqlite3.connect(TITLES_SQLITE)
    rows = conn.execute(
        "SELECT id, poster FROM titles "
        "WHERE category = 'chinese' AND poster LIKE 'https://images.ijavtorrent.com%'"
    ).fetchall()
    if limit:
        rows = rows[:limit]
    conn.close()

    manifest = {}
    ok = fail = 0
    t0 = time.time()
    for idx, (title_id, poster_url) in enumerate(rows, 1):
        code = code_from_poster_url(poster_url)
        dest = os.path.join(POSTERS_DIR, f"{code}.jpg")
        if download(poster_url, dest):
            manifest[title_id] = code
            ok += 1
        else:
            fail += 1
        if idx % 50 == 0 or idx == len(rows):
            print(f"  {idx}/{len(rows)} (ok={ok}, fail={fail}) "
                  f"[{time.time() - t0:.0f}s]")
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"host_posters: downloaded={ok}, failed={fail} "
            f"({time.time() - t0:.0f}s) -> {POSTERS_DIR}")


if __name__ == "__main__":
    main()

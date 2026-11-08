#!/usr/bin/env python3
"""Download Korean AV covers and re-host them on GitHub raw (same pattern as Chinese).

- `kr:ijav-<id>` rows: iJavTorrent covers are deterministic
  (`https://images.ijavtorrent.com/data/covers/<id>.jpg`, hotlink-protected,
  needs `Referer: https://www.ijavtorrent.com/`). Download into
  `<repo>/posters/kr/ijav-<id>.jpg` and rewrite the DB `poster` to the
  GitHub raw URL so Stremio fetches the image directly.
- `kr:forumkorean-<tid>` rows: fetch the thread page (board URL with slug)
  and extract a plausible cover image (jpg/png/webp, excluding forum UI
  assets like `empty.gif` / icon gifs). Download into
  `<repo>/posters/kr/forumkorean-<tid>.jpg` (Referer: forumkorean) and
  rewrite the DB `poster` to the raw URL. Threads with no cover image are
  left empty (no reliable source).

Resume-safe: rows whose poster already points at GitHub raw are skipped;
already-downloaded local files are not re-fetched.

Usage (from repo root):
    python3 addon/host_korean_posters.py             # all korean rows
    python3 addon/host_korean_posters.py --limit 10  # first N rows only
"""

import argparse
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request

from build_addon import TITLES_SQLITE
import enrich_korean as ek

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KR_POSTERS_DIR = os.path.join(REPO, "posters", "kr")
RAW_BASE = "https://raw.githubusercontent.com/borepig/javstreamio/main/posters/kr"
REFERER_IJAV = "https://www.ijavtorrent.com/"
REFERER_FORUM = "https://adulto.forumkorean.com/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
DELAY = float(os.environ.get("KOREAN_POSTER_DELAY", "0.3"))
# Forum UI / decorative assets that must not be used as a cover.
UI_IMG_RE = re.compile(
    r"empty\.gif|icon_.*\.(gif|png)$|prosilver|/i/fa/", re.I
)
COVER_EXT_RE = re.compile(r"\.(jpe?g|png|webp)$", re.I)


def http_get(url: str, referer: str, timeout: int = 30) -> str:
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Referer": referer}
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 500, 502, 503) and attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            raise
        except OSError:
            if attempt < 2:
                time.sleep(1 + attempt)
                continue
            raise
    return ""


def http_get_bytes(url: str, referer: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def forum_thread_urls() -> dict:
    """thread id -> full /t<id>-<slug> path, from the two forum boards."""
    threads = {}
    for path, _label in ek.FK_BOARDS:
        board_html = ek.http_get(ek.FK_BASE + path)
        for full, tid in re.findall(r'href="(/t(\d+)-[^"]*)"', board_html):
            threads.setdefault(tid, full)
    return threads


def pick_forum_cover(thread_html: str) -> str:
    """First plausible cover image in a forumkorean thread (non-UI, raster)."""
    for url in re.findall(r'<img[^>]+(?:src|data-src)="([^"]+)"', thread_html):
        if not COVER_EXT_RE.search(url):
            continue
        if UI_IMG_RE.search(url):
            continue
        return url
    return ""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=0, help="Process at most N korean rows (0 = all).")
    args = ap.parse_args()

    os.makedirs(KR_POSTERS_DIR, exist_ok=True)
    conn = sqlite3.connect(TITLES_SQLITE)
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT id, poster FROM titles WHERE category = 'korean' ORDER BY id"
    ).fetchall()
    if args.limit:
        rows = rows[: args.limit]

    forum_urls = forum_thread_urls()
    updated = downloaded = failed = 0
    for rid, poster in rows:
        if poster and poster.startswith(RAW_BASE):
            continue  # already hosted
        m_ijav = re.match(r"kr:ijav-(\d+)", rid)
        m_fk = re.match(r"kr:forumkorean-(\d+)", rid)
        src_url = ""
        referer = ""
        if m_ijav:
            src_url = f"https://images.ijavtorrent.com/data/covers/{m_ijav.group(1)}.jpg"
            referer = REFERER_IJAV
        elif m_fk:
            slug = forum_urls.get(m_fk.group(1))
            if slug:
                src_url = pick_forum_cover(ek.http_get(ek.FK_BASE + slug))
                referer = REFERER_FORUM
        if not src_url:
            failed += 1
            continue
        dest = os.path.join(KR_POSTERS_DIR, f"{rid.replace(':', '')}.jpg")
        if not os.path.exists(dest):
            try:
                with open(dest, "wb") as f:
                    f.write(http_get_bytes(src_url, referer))
            except Exception as e:
                print(f"  {rid}: download failed ({src_url[:60]}): {e}")
                failed += 1
                continue
        downloaded += 1
        cur.execute(
            "UPDATE titles SET poster = ? WHERE id = ?",
            (f"{RAW_BASE}/{rid.replace(':', '')}.jpg", rid),
        )
        updated += cur.rowcount
        time.sleep(DELAY)
    conn.commit()
    conn.close()
    print(
        f"host_korean_posters: updated={updated}, downloaded={downloaded}, "
        f"failed_or_empty={failed}"
    )


if __name__ == "__main__":
    main()

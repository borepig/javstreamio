#!/usr/bin/env python3
"""Fill proper posters for hk:cat3 rows that only have the 1024xp.com-served
forum-preview poster (empty DMM poster).

For every hk:cat3 row whose `poster` is empty: clean the forum-thread title
(strip (...) / [ ... ] noise), search SearXNG (default engine set — the
google-cse + wikipedia restriction returns 0 results because those engines
are rate-limited) and fall back to the self-hosted Firecrawl /v2/search.
The best movie page is ranked Wikipedia > TMDB > Douban > IMDB > AvGood;
the page is scraped via Firecrawl /v2/scrape (rawHtml) and its og:image is
extracted. The poster URL is accepted only when its host is on the app's
poster allow-list (so it survives withPublicPosters) and is verified to be
a fetchable image before being written to the DB.

This supersedes the older douban-subject_suggest + SVG-card version of this
job: douban is now just one of the ranked sources.

Resume-safe: rows that already have a `poster` are skipped, so the job can
be re-run after interruptions.

Usage:
    python3 addon/hk_cat3_posters.py [--limit N] [--delay SEC] [--dry-run]
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "debrid-app" / "data" / "titles.sqlite"
SEARXNG = os.environ.get("SEARXNG_URL", "http://192.168.68.83:8888")
FIRECRAWL_API = os.environ.get("FIRECRAWL_API_URL", "http://192.168.68.83:3002")

# Mirrors POSTER_HOSTS in debrid-app/lib/addon-data.js (plus the two
# movie-DB CDN hosts this job stores). A stored poster on any other host
# would be stripped by withPublicPosters, so it is rejected before the DB
# write.
ALLOWED_POSTER_HOSTS = [
    "tu.djhdhs.us",
    "jdbstatic.com",
    "hanime-cdn.com",
    "raw.githubusercontent.com",
    "doubanio.com",
    "img.douban.com",
    "javstremio.ddns.net",
    "upload.wikimedia.org",
    "avgood.com",
    "media.themoviedb.org",
    "m.media-amazon.com",
]


def host_allowed(url: str) -> bool:
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
        return any(host == a or host.endswith("." + a) for a in ALLOWED_POSTER_HOSTS)
    except Exception:
        return False


def clean_query(title: str) -> str:
    """Strip forum-thread noise: (中文字幕高清完整版) / [RMVB/935M/BT]."""
    q = re.sub(r"\(.*?\)", "", title or "")
    q = re.sub(r"\[.*?\]", "", q)
    return " ".join(q.split())


def sxng_web(query: str, count: int = 8) -> list:
    """SearXNG web search using the default engine set (no `engines` param;
    the google-cse + wikipedia restriction is broken/rate-limited)."""
    params = urllib.parse.urlencode({"q": query, "format": "json"})
    try:
        with urllib.request.urlopen(f"{SEARXNG}/search?{params}", timeout=30) as r:
            d = json.loads(r.read())
        return (d.get("results") or [])[:count]
    except Exception as e:
        print(f"    searxng error: {e}")
        return []


def fc_search(query: str, limit: int = 8) -> list:
    body = json.dumps({"query": query, "limit": limit}).encode()
    try:
        req = urllib.request.Request(FIRECRAWL_API + "/v2/search", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read())
        if not d.get("success"):
            print(f"    firecrawl search: {d.get('message')}")
            return []
        return ((d.get("data") or {}).get("web") or [])[:limit]
    except Exception as e:
        print(f"    firecrawl search error: {e}")
        return []


def rank_src(r) -> int:
    u = (r.get("url") or "").lower()
    if "wikipedia.org" in u:
        return 0
    if "themoviedb.org" in u:
        return 1
    if "douban" in u:
        return 2
    if "imdb.com" in u:
        return 3
    if "avgood.com" in u:
        return 4
    return 9


def pick_sources(results):
    """Ranked candidate movie pages; keeps every rank<9 candidate so a
    blocked page (IMDB/Douban often block scrapers) falls through to the
    next candidate."""
    res = [r for r in results if r.get("url")]
    return sorted(res, key=rank_src)


def fc_scrape_raw(url: str) -> str:
    body = json.dumps({"url": url, "formats": ["rawHtml"]}).encode()
    try:
        req = urllib.request.Request(FIRECRAWL_API + "/v2/scrape", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.loads(r.read())
        if not d.get("success"):
            return ""
        data = d.get("data") or {}
        return data.get("rawHtml") or data.get("html") or ""
    except Exception as e:
        print(f"    firecrawl scrape error: {e}")
        return ""


def og_image(html: str) -> str:
    """Extract og:image from rawHtml; handles both attribute orders."""
    m = re.search(r'<meta[^>]*?property="og:image"[^>]*>', html)
    if m:
        c = re.search(r'content="([^"]+)"', m.group(0))
        return c.group(1) if c else ""
    m = re.search(r'<meta[^>]*?content="([^"]+)"[^>]*?property="og:image"', html)
    return m.group(1) if m else ""


def verify(url: str) -> bool:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://sehuatang.org/"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            ct = (r.headers.get("Content-Type") or "")
            head = r.read(2048)
        return ct.startswith("image/") and len(head) > 1024
    except Exception:
        return False


# JavDB-403-style escalating backoff state (AGENTS.md pattern): consecutive
# search failures (rate-limited backends) escalate the sleep 15s -> 30s ->
# 60s -> ... capped at 300s; any successful search resets the streak. Rows
# that still fail are skipped (not written), so a later resume retries them.
_SEARCH_FAIL_STREAK = 0
SEARCH_BACKOFF_BASE = 15
SEARCH_BACKOFF_CAP = 300


def search_with_backoff(query: str) -> list:
    global _SEARCH_FAIL_STREAK
    results = sxng_web(query)
    if not results:
        results = fc_search(query)
    if results:
        _SEARCH_FAIL_STREAK = 0
        return results
    delay = min(SEARCH_BACKOFF_BASE * (2 ** _SEARCH_FAIL_STREAK), SEARCH_BACKOFF_CAP)
    _SEARCH_FAIL_STREAK += 1
    print(f"    [search] 0 results (fail streak={_SEARCH_FAIL_STREAK}), backing off {delay}s", flush=True)
    time.sleep(delay)
    results = sxng_web(query) or fc_search(query)
    if results:
        _SEARCH_FAIL_STREAK = 0
    return results


def find_poster(query: str):
    """SearXNG search (Firecrawl fallback, escalating backoff on 0 results),
    then per-candidate scrape + extract + allow-list check + verify.
    Returns (poster_url, status) with status in {"found", "no_result",
    "no_usable"}."""
    if not (query or "").strip():
        return "", "no_result"
    results = search_with_backoff(query)
    if not results:
        return "", "no_result"
    for src in pick_sources(results):
        url = src.get("url") or ""
        poster = ""
        if host_allowed(url):
            html = fc_scrape_raw(url)
            poster = og_image(html)
        if not poster or not host_allowed(poster):
            continue
        if not verify(poster):
            continue
        return poster, "found"
    return "", "no_usable"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = sqlite3.connect(str(DB))
    rows = db.execute(
        "SELECT id, name FROM titles "
        "WHERE id LIKE 'hk:cat3-%' AND (poster IS NULL OR poster = '') "
        "ORDER BY posted_at DESC"
    ).fetchall()
    total = len(rows)
    if args.limit:
        rows = rows[: args.limit]

    filled = no_result = no_usable = 0
    for i, (row_id, name) in enumerate(rows):
        q = clean_query(name)
        poster, status = find_poster(q)
        if status == "found":
            if not args.dry_run:
                db.execute(
                    "UPDATE titles SET poster = ?, background = ? WHERE id = ?",
                    (poster, poster, row_id),
                )
                db.commit()
            filled += 1
            print(f"[hkcat3posters] {i + 1}/{len(rows)} {row_id}: {poster[:80]}")
        else:
            if status == "no_result":
                no_result += 1
            else:
                no_usable += 1
            print(f"[hkcat3posters] {i + 1}/{len(rows)} {row_id}: {status} for '{q}'")
        if args.delay:
            time.sleep(args.delay)

    db.close()
    print(
        f"done: filled={filled} no_result={no_result} "
        f"no_usable={no_usable} (of {len(rows)} rows, total {total})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

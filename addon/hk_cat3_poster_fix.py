#!/usr/bin/env python3
"""HK Cat III poster fix — replace SVG title-card posters/fanart with real images.

For every hongkong title whose poster or background (fanart) is the
self-hosted SVG card (javstremio.ddns.net/posters/hkcat3/*.svg — the
"name + year" cards the user does not want), find a real poster using the
Chinese name only (user instruction: search the name, never the English
name):

1. AvGood direct search (engine-independent): /c/s/?q=<name> -> detail
   page -> /remote/pic poster (avgood.com, public host).
2. SearXNG "<name>" (name only, no year): take the top result URLs.
   zh.wikipedia.org pages are fetched directly (og:image on
   upload.wikimedia.org); every other URL is scraped through the
   self-hosted Firecrawl v2 API (html format), then the poster is read
   from og:image or the first content <img>.
3. Only posters on the live addon's allow-listed hosts are accepted
   (the same hosts isPublicArtUrl / POSTER_HOSTS accept in
   debrid-app/lib/addon-data.js); anything else would be silently
   stripped by withPublicPosters, so it counts as "not found".
4. Rows with a real poster but an SVG-card fanart get their background
   replaced by the found poster. Rows with an SVG poster and no real
   poster found after the full search effort are DELETED (user
   instruction: "if you can't find it, delete all movies without a
   poster").

Usage:
    python3 addon/hk_cat3_poster_fix.py [--limit N] [--delay SEC] [--dry-run]
"""

import argparse
import html as html_mod
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
META_JSON = ROOT / "addon" / "hk_cat3_av.json"
DB = ROOT / "debrid-app" / "data" / "titles.sqlite"
SEARXNG = os.environ.get("SEARXNG_URL", "http://192.168.68.83:8888")
FIRECRAWL_API = os.environ.get("FIRECRAWL_API_URL", "http://192.168.68.83:3002")
AVGOOD = "https://avgood.com"
LIVE_HOST_PREFIX = "https://javstremio.ddns.net"

# Posters must live on a host the live addon allow-lists (isPublicArtUrl +
# POSTER_HOSTS in debrid-app/lib/addon-data.js), otherwise
# withPublicPosters strips the field from catalog/meta. The live host is
# excluded: it only serves the SVG title cards we are replacing.
PUBLIC_ART_HOSTS = [
    "pics.dmm.co.jp",
    "hanime-cdn.com",
    "fourhoi.com",
    "image.mgstage.com",
    "jdbstatic.com",
    "avgood.com",
    "images.ijavtorrent.com",
    "raw.githubusercontent.com",
    "upload.wikimedia.org",
    "doubanio.com",
    "img.douban.com",
    "image.oceanveil.net",
    "hentai.tv",
]


def fetch_page(url: str) -> str:
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (javstreamio)"})
        with urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        return ""


def search_sxng(query: str, num: int = 10) -> tuple:
    """SearXNG JSON search. Returns (results, searched_ok):

    - searched_ok True: the engine answered; results may be empty (a real
      "no poster found" verdict).
    - searched_ok False: the upstream engines were rate-limited/unreachable
      even after retries (5s/10s/20s on 0-result, 15s->300s backoff capped
      at 4 transport retries) — NOT proof a poster doesn't exist; the row
      must be kept and re-tried later."""
    url = f"{SEARXNG}/search?q={urllib.parse.quote(query)}&format=json&pageno=1"
    backoff = 15
    zero_tries = 0
    zero_wait = 5
    transport_tries = 0
    while True:
        try:
            req = Request(url, headers={"User-Agent": "javstreamio-hkcat3poster/1.0"})
            with urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
            results = data.get("results") or []
            if results:
                return results, True
            zero_tries += 1
            if zero_tries > 3:
                print("    sxng 0 results after 3 retries (engines rate-limited)")
                return [], False
            print(f"    sxng 0 results; retry {zero_tries}/3 in {zero_wait}s")
            time.sleep(zero_wait)
            zero_wait = min(zero_wait * 2, 30)
        except Exception as e:
            transport_tries += 1
            if transport_tries > 4:
                print(f"    sxng unreachable after {transport_tries} retries ({e})")
                return [], False
            print(f"    sxng error: {e}; retrying in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)


def firecrawl_scrape_html(url: str) -> tuple:
    """Scrape one URL through the self-hosted Firecrawl v2 API.

    Returns (html, ok): ok=False when the scrape itself failed (HTTP 500,
    timeout) even after one retry — a failed scrape is NOT proof a poster
    doesn't exist, so the caller must keep the row and retry later."""
    body = json.dumps({"url": url, "formats": ["html"]}).encode()
    for attempt in (1, 2):
        try:
            req = Request(
                FIRECRAWL_API + "/v2/scrape",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            with urlopen(req, timeout=90) as r:
                data = json.loads(r.read())
            if not data.get("success"):
                return "", False
            return (data.get("data") or {}).get("html") or "", True
        except Exception as e:
            if attempt == 1:
                print(f"    firecrawl error: {e}; retrying in 3s")
                time.sleep(3)
                continue
            print(f"    firecrawl error: {e}")
            return "", False


def og_image(html_text: str) -> str:
    m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html_text)
    if not m:
        m = re.search(r'<meta[^>]+content="([^"]+)"[^>]+property="og:image"', html_text)
    if m:
        return html_mod.unescape(m.group(1)).strip()
    return ""


def host_allowed(url: str) -> bool:
    """True when the image URL's host is in the live addon's allow-list."""
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return False
    return any(host == a or host.endswith("." + a) for a in PUBLIC_ART_HOSTS)


AVGOOD_RESULT_RE = re.compile(r'<a href="/c/(\d+)\.html"[^>]*>(.*?)</a>', re.S)


def avgood_search_ids(name: str) -> list:
    page = fetch_page(f"{AVGOOD}/c/s/?q={urllib.parse.quote(name)}")
    body = page[page.find("<body"):] if page else ""
    ids = []
    for m in AVGOOD_RESULT_RE.finditer(body):
        if m.group(1) not in ids:
            ids.append(m.group(1))
    return ids


def avgood_detail_poster(rid: str) -> str:
    page = fetch_page(f"{AVGOOD}/c/{rid}.html")
    if not page:
        return ""
    m = re.search(r'(/remote/pic/[^"\s]+)', page)
    return AVGOOD + m.group(1) if m else ""


SXXNG_DOWN = object()  # sentinel: search engine was rate-limited; no verdict


def find_poster(name: str):
    """Find a real, allow-listed poster for a title.

    Order: AvGood direct search (engine-independent) -> SearXNG name-only
    search -> wiki direct fetch / Firecrawl scrapes.

    Returns the poster URL when found, None when searched-and-not-found
    (delete the row), or SXXNG_DOWN when the SearXNG engines were
    rate-limited (keep the row and retry later)."""
    for rid in avgood_search_ids(name)[:4]:
        p = avgood_detail_poster(rid)
        if p:
            return p
        time.sleep(0.3)
    results, searched_ok = search_sxng(name)
    if not searched_ok:
        return SXXNG_DOWN
    scrape_failed = False
    for r in results[:5]:
        url = (r.get("url") or "").strip()
        if not url:
            continue
        if "zh.wikipedia.org" in url:
            html = fetch_page(url)
            img = og_image(html)
            if img and host_allowed(img):
                return img
        else:
            html, scrape_ok = firecrawl_scrape_html(url)
            if not scrape_ok:
                scrape_failed = True
            img = og_image(html)
            if not img:
                m = re.search(r'<img[^>]+src="([^"]+)"', html)
                img = html_mod.unescape(m.group(1)) if m else ""
            if img and host_allowed(img):
                return img
        time.sleep(0.3)
    if scrape_failed:
        # At least one scrape failed (500/timeout): the search itself was
        # incomplete, so keep the row (SVG card stays) and retry later.
        return SXXNG_DOWN
    return None


def process_title(idx: int, t: dict, db, total: int, dry_run: bool) -> str:
    row_id = f"hk:cat3-{idx}"
    row = db.execute("SELECT poster, background FROM titles WHERE id = ?", (row_id,)).fetchone()
    if not row:
        print(f"[hkcat3poster] {idx + 1}/{total} {row_id}: row missing, skipped")
        return "skip"
    poster = row[0] or ""
    bg = row[1] or ""
    poster_svg = poster.startswith(LIVE_HOST_PREFIX)
    bg_svg = bg.startswith(LIVE_HOST_PREFIX)
    if not poster_svg and not bg_svg:
        return "ok"

    # Search by the Chinese name only (user: not the English name).
    name = (t.get("zh") or "").strip()
    if not name:
        name = (t.get("en") or "").strip()
    found = find_poster(name)
    if found is SXXNG_DOWN:
        # SearXNG rate-limited and AvGood empty: keep the row (SVG card
        # stays) so a later run, once the engines recover, can find the
        # poster. Not a "not found" verdict — the search itself was
        # unavailable.
        print(f"[hkcat3poster] {idx + 1}/{total} {row_id} {name}: sxng rate-limited, kept for retry")
        return "kept_pending"
    if found:
        updates = {}
        if poster_svg:
            updates["poster"] = found
        if bg_svg:
            updates["background"] = found
        if updates:
            if not dry_run:
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                db.execute(
                    f"UPDATE titles SET {set_clause} WHERE id = ?",
                    (*updates.values(), row_id),
                )
                db.commit()
            print(f"[hkcat3poster] {idx + 1}/{total} {row_id} {name}: fixed -> {found[:70]}")
        return "fixed"
    if not dry_run:
        db.execute("DELETE FROM titles WHERE id = ?", (row_id,))
        db.commit()
    print(f"[hkcat3poster] {idx + 1}/{total} {row_id} {name}: no real poster found -> deleted")
    return "deleted"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    meta = json.loads(META_JSON.read_text(encoding="utf-8"))
    titles = meta["av"]
    total = len(titles)
    if args.limit:
        titles = titles[:args.limit]

    db = sqlite3.connect(str(DB))
    stats = {"ok": 0, "fixed": 0, "deleted": 0, "kept_pending": 0, "skip": 0}
    for idx, t in enumerate(titles):
        res = process_title(idx, t, db, total, args.dry_run)
        stats[res] = stats.get(res, 0) + 1
        if args.delay:
            time.sleep(args.delay)
    db.close()
    print(f"done: {json.dumps(stats)} (of {min(len(meta['av']), args.limit or len(meta['av']))} titles)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Build a catalog of every Hentaistream episode (title, episode#, direct mp4).

Hentaistream (tube.hentaistream.com) is a WordPress site whose full episode
catalog is enumerable via its sitemaps. This script:

1. Fetches the sitemap index + sub-sitemaps in parallel and collects every
   episode URL (`/ <base>-episode-NN` or `/ <base>-ep-N`).
2. For each episode URL (in parallel, resumable) fetches the page, extracts
   the og:title (series name), the iframe player, then the frame HTML for the
   direct mp4 source.
3. Writes `addon/.hentaistream_catalog.json` (a list of
   {slug, title, episode, mp4, runtime}) incrementally so a re-run only
   fetches what is still missing.

Usage:
  python3 addon/build_hentaistream_catalog.py            # full build (resumable)
  python3 addon/build_hentaistream_catalog.py --limit 25 # small sample
  python3 addon/build_hentaistream_catalog.py --refresh  # refetch sitemaps

The catalog feeds `enrich_hentai.py` so hnt rows can be matched by series
title and given a direct `stream_url`.
"""

import argparse
import concurrent.futures
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(ROOT, "addon", ".hentaistream_catalog.json")
EP_URLS = os.path.join(ROOT, "addon", ".hentaistream_ep_urls.json")

BASE = "https://tube.hentaistream.com"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
WORKERS = int(os.environ.get("HS_WORKERS", "8"))
PAGE_TIMEOUT = int(os.environ.get("HS_PAGE_TIMEOUT", "25"))
DELAY = float(os.environ.get("HS_DELAY", "0.2"))


def http_get(url, referer=None, timeout=PAGE_TIMEOUT):
    headers = {"User-Agent": UA}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def load_catalog():
    if os.path.exists(CATALOG):
        try:
            with open(CATALOG, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def save_catalog(catalog, lock=None):
    def _write():
        with open(CATALOG, "w", encoding="utf-8") as f:
            json.dump(catalog, f)
    if lock:
        with lock:
            _write()
    else:
        _write()


def collect_episode_urls(refresh=False):
    """Return the list of episode URLs, cached in .hentaistream_ep_urls.json.

    Unless --refresh is passed, a cached URL list is loaded directly so the
    (expensive) 223-sub-sitemap collection only happens on first run / refresh.
    """
    if not refresh and os.path.exists(EP_URLS):
        try:
            with open(EP_URLS, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    index = http_get(BASE + "/sitemap.xml")
    subs = re.findall(r"<loc>([^<]+)</loc>", index)
    urls = []
    lock = threading.Lock()

    def fetch_sub(loc):
        try:
            body = http_get(loc)
            found = re.findall(r"<loc>([^<]+)</loc>", body)
            with lock:
                urls.extend(found)
        except (urllib.error.HTTPError, OSError):
            pass

    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(fetch_sub, subs))

    ep_urls = [
        u for u in urls
        if re.search(r"(episode|ep)-\d+", u)
    ]
    ep_urls = sorted(set(ep_urls))
    with open(EP_URLS, "w", encoding="utf-8") as f:
        json.dump(ep_urls, f)
    return ep_urls


def parse_episode_url(url):
    m = re.search(r"/([^/]+)-(episode|ep)-(\d+)/?$", url)
    if not m:
        return None, None
    return m.group(1), int(m.group(3))


def clean_og_title(og):
    t = re.sub(r"^Watch\s+", "", (og or "").strip())
    t = re.sub(r"\s+Episode\s+\d+$", "", t, flags=re.I)
    return t.strip()


def build_one(url, catalog, done_slugs, lock):
    base, episode = parse_episode_url(url)
    if base is None:
        return
    if base in done_slugs:
        return
    try:
        page = http_get(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return
        raise
    except OSError:
        return
    time.sleep(DELAY)
    og = re.search(r'<meta property="og:title" content="([^"]*)"', page)
    title = clean_og_title(og.group(1)) if og else base
    runtime = ""
    rm = re.search(r">\s*(\d{1,2}:\d{2}(?::\d{2})?)</span>", page)
    if rm:
        runtime = f"{int(rm.group(1).split(':')[0])} min"
    iframe = re.search(r'<iframe[^>]*src="([^"]+)"', page)
    mp4 = ""
    if iframe:
        src = iframe.group(1)
        if not src.startswith("http"):
            src = BASE + src
        try:
            frame = http_get(src, referer=url, timeout=PAGE_TIMEOUT)
            time.sleep(DELAY)
            m2 = re.search(r'<source[^>]*src="([^"]+\.mp4)"', frame, re.I)
            if m2:
                mp4 = m2.group(1)
        except (urllib.error.HTTPError, OSError):
            mp4 = ""
    with lock:
        catalog.append({"slug": base, "title": title, "episode": episode,
                        "mp4": mp4, "runtime": runtime})
        # Caller already holds `lock`; write directly (no re-acquire, or the
        # non-reentrant lock self-deadlocks the worker thread).
        save_catalog(catalog)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--refresh", action="store_true", help="Refetch sitemaps and rebuild from scratch.")
    args = ap.parse_args()

    if args.refresh and os.path.exists(CATALOG):
        os.remove(CATALOG)

    catalog = load_catalog()
    done_slugs = {c["slug"] for c in catalog}

    ep_urls = collect_episode_urls()
    if args.limit:
        ep_urls = ep_urls[:args.limit]

    todo = [u for u in ep_urls if parse_episode_url(u)[0] not in done_slugs]
    print(f"episode URLs: {len(ep_urls)}, already cached: {len(done_slugs)}, todo: {len(todo)}")

    lock = threading.Lock()
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(build_one, u, catalog, done_slugs, lock) for u in todo]
        for _ in concurrent.futures.as_completed(futs):
            pass

    with lock:
        save_catalog(catalog)
    print(f"done: catalog now {len(catalog)} entries "
          f"({time.time() - t0:.0f}s)")
    with_mp4 = sum(1 for c in catalog if c.get("mp4"))
    print(f"entries with a direct mp4: {with_mp4}/{len(catalog)}")


if __name__ == "__main__":
    main()

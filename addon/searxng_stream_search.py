#!/usr/bin/env python3
"""Use the local SearXNG instance (QNAP) to find additional streaming
resources for the 12 debrid-only hnt series.

For each series it:
  1. Queries SearXNG web + video engines with the series' English title.
  2. Collects candidate URLs from the results.
  3. Fetches each candidate page and looks for a directly playable video
     (mp4 / m3u8 / <video> source) so the row can get a `stream_url`.

Usage:
  python3 addon/searxng_stream_search.py --search          # run searches, dump candidates
  python3 addon/searxng_stream_search.py --probe            # fetch candidate pages, detect video URLs
  python3 addon/searxng_stream_search.py --apply             # write found stream_url into titles.sqlite
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
import urllib.error

SEARXNG = os.environ.get("SEARXNG_URL", "http://192.168.68.83:8888")
DB = os.path.join(os.path.dirname(__file__), "..", "debrid-app", "data", "titles.sqlite")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
CANDIDATES_JSON = os.path.join(os.path.dirname(__file__), ".searxng_stream_candidates.json")
FOUND_JSON = os.path.join(os.path.dirname(__file__), ".searxng_stream_found.json")
DELAY = float(os.environ.get("SEARXNG_DELAY", "0.5"))
WEB_ENGINES = "google cse,brave,duckduckgo"
VIDEO_ENGINES = "youtube,google videos,bing videos"

# series key -> (hnt id prefix filter, search query)
SERIES = [
    ("Unexpectedly Naughty Fukami", "Unexpectedly Naughty Fukami hentai watch online"),
    ("My Classmate's a Sexy Actress", "My Classmate's a Sexy Actress, and Now We Live Together! hentai"),
    ("Secret Mission", "Secret Mission Undercover Agents Never Back Down hentai"),
    ("Ren Arisugawa", "Arisugawa Ren tte Honto wa Onna Nanda yo ne. hentai watch"),
    ("Taisho", "Taisho Era Contract Marriage: The Substitute Bride and a Soldier's Fierce Love"),
    ("What She Fell On", "What She Fell on Was the Tip of My Dick hentai"),
    ("Share House", "The Share House's Secret Rule hentai"),
    ("Overflow", "Overflow hentai series watch"),
    ("Click Me All Over", "Mouse Cursor, I'm Gonna Click Away On The Girls! hentai"),
    ("KAMUI", "KAMUI He's Behind You hentai"),
    ("Demon Queen Melissa", "Onna Maou Melissa no H na Boukenki: Seieki o Atsumeru Ero Maou"),
    ("I Have No Money", "Okane ga Nai hentai"),
    ("Lemon Angel", "Lemon Angel hentai"),
]

# Only keep candidate URLs from hosts that plausibly host video (streaming/AV sites).
HOST_HINTS = re.compile(
    r"(anime|hd|stream|video|hentai|jav|anime|nyaa|sukebei|hanime|underhentai|lulu|115|megashare|animeidh|aniq|tooniverse)",
    re.I,
)


def http_get(url, referer=None, timeout=30):
    headers = {"User-Agent": UA, "Accept": "text/html"}
    if referer:
        headers["Referer"] = referer
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            if e.code == 403 and attempt < 2:
                time.sleep(1 + attempt)
                continue
            raise
        except Exception:
            if attempt < 2:
                time.sleep(1 + attempt)
                continue
            raise
    return ""


def searxng_search(query, engines, pageno=1, count=10):
    params = {
        "q": query,
        "format": "json",
        "engines": engines,
        "pageno": str(pageno),
        "count": str(count),
    }
    url = f"{SEARXNG}/search?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    results = data.get("results", [])
    return [
        {"url": res.get("url", ""), "title": res.get("title", ""), "content": (res.get("content") or "")[:200]}
        for res in results
    ]


def do_search():
    all_candidates = []
    for key, query in SERIES:
        cands = []
        for engines, label in [(WEB_ENGINES, "web"), (VIDEO_ENGINES, "video")]:
            try:
                res = searxng_search(query, engines)
            except Exception as e:
                print(f"  SearXNG {label} search failed for '{key}': {e}", flush=True)
                continue
            for r in res:
                if r["url"]:
                    cands.append({"series": key, "engine": label, "title": r["title"], "url": r["url"], "content": r["content"]})
            time.sleep(DELAY)
        all_candidates.extend(cands)
        host_hit = [c for c in cands if HOST_HINTS.search(c["url"]) or HOST_HINTS.search(c["title"] or "")]
        print(f"{key}: {len(cands)} candidates ({len(host_hit)} host-hits)")
    with open(CANDIDATES_JSON, "w", encoding="utf-8") as f:
        json.dump(all_candidates, f, ensure_ascii=False, indent=1)
    print(f"Wrote {len(all_candidates)} candidates -> {CANDIDATES_JSON}")
    return all_candidates


# Video detection on a candidate page.
VIDEO_URL_RE = re.compile(
    r"(https?://[^\"'\s?]+?\.(?:mp4|webm|mov|m3u8)(?:\?[^\"'\s]+)?)"
)
VIDEO_TAG_RE = re.compile(r"<video\b[^>]*\bsrc=[\"']([^\"']+)[\"']")
SOURCE_RE = re.compile(r"<source[^>]*\bsrc=[\"']([^\"']+)[\"']", re.I)
EMBED_RE = re.compile(r"https?://[^\"'\s]+?\.(?:mp4|webm|m3u8)(?:\?[^\"'\s]+)?", re.I)


# Domains known to embed directly playable video (mp4/m3u8). Probe these first.
PREFERRED_DOMAINS = [
    "hanime.tv", "watchhentai.net", "hentaimama.io", "hentaigasm.com",
    "hentaistream.com", "hdhentai.tv", "miohentai.com", "hentaiera.com",
    "hentaihd.net", "hentaihaven.xxx", "hentaicity.com", "hentaipaw.com",
    "hentaigasm.com", "reanime.to", "animeoshi.com", "anime-planet.com",
    "hentaiera.com", "hentaigasm.com",
]

def probe_url(url, referer=None, timeout=10):
    try:
        html = http_get(url, referer=referer, timeout=timeout)
    except Exception as e:
        print(f"  probe failed {url}: {e}", flush=True)
        return None
    found = []
    for m in VIDEO_URL_RE.finditer(html):
        found.append(m.group(1))
    for m in VIDEO_TAG_RE.finditer(html):
        found.append(m.group(1))
    for m in SOURCE_RE.finditer(html):
        found.append(m.group(1))
    if not found:
        for m in EMBED_RE.finditer(html):
            found.append(m.group(1))
    # de-dup, keep unique
    seen = set()
    unique = []
    for u in found:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


def _domain(url):
    return urllib.parse.urlparse(url).netloc.lower()

def do_probe():
    with open(CANDIDATES_JSON, "r", encoding="utf-8") as f:
        cands = json.load(f)
    # Prioritize pages from known video-hosting domains; cap probes per series.
    MAX_PROBE_PER_SERIES = 6
    per_series = {}
    for c in cands:
        per_series.setdefault(c["series"], []).append(c)
    to_probe = []
    for series, items in per_series.items():
        pref = [c for c in items if any(_domain(c["url"]).endswith(d) for d in PREFERRED_DOMAINS)
                 or re.search(r"\.(mp4|m3u8)", c["url"])]
        other = [c for c in items if c not in pref]
        chosen = (pref + other)[:MAX_PROBE_PER_SERIES]
        to_probe.extend(chosen)
    found = []
    for c in to_probe:
        videos = probe_url(c["url"], referer=c.get("referer"))
        if videos:
            found.append({"series": c["series"], "page": c["url"], "video_urls": videos})
            print(f"  FOUND {len(videos)} video URL(s) for '{c['series']}' at {c['url']}", flush=True)
            for v in videos:
                print(f"      {v}", flush=True)
        time.sleep(DELAY)
    with open(FOUND_JSON, "w", encoding="utf-8") as f:
        json.dump(found, f, ensure_ascii=False, indent=1)
    print(f"{len(found)} pages with video URLs -> {FOUND_JSON}", flush=True)
    return found


def do_apply():
    if not os.path.exists(FOUND_JSON):
        print("Run --probe first.")
        return
    with open(FOUND_JSON, "r", encoding="utf-8") as f:
        found = json.load(f)
    con = sqlite3.connect(os.path.normpath(DB))
    cur = con.cursor()
    cols = [r[1] for r in cur.execute("PRAGMA table_info(titles)").fetchall()]
    if "stream_url" not in cols:
        cur.execute("ALTER TABLE titles ADD COLUMN stream_url TEXT DEFAULT ''")
        con.commit()
    updated = 0
    for item in found:
        series = item["series"]
        page = item["page"]
        videos = item["video_urls"]
        if not videos:
            continue
        best = videos[0]
        rows = cur.execute("SELECT id FROM titles WHERE id LIKE 'hnt:%' AND COALESCE(stream_url,'')=''").fetchall()
        # Match rows whose name contains the series key fragment.
        frag = SERIES_KEY_FRAGMENT.get(series)
        if not frag:
            continue
        for (rid,) in rows:
            name = cur.execute("SELECT name FROM titles WHERE id=?", (rid,)).fetchone()
            if name and frag.lower() in (name[0] or "").lower():
                cur.execute("UPDATE titles SET stream_url=? WHERE id=?", (best, rid))
                updated += 1
    con.commit()
    con.close()
    print(f"Applied {updated} stream_url rows.")


# Series key -> substring to match against the hnt row name.
SERIES_KEY_FRAGMENT = {
    "Unexpectedly Naughty Fukami": "Yatara Yarashii Fukami-kun",
    "My Classmate's a Sexy Actress": "My Classmate",
    "Secret Mission": "Secret Mission",
    "Ren Arisugawa": "Ren Arisugawa",
    "Taisho": "Taishou Itsuwari",
    "What She Fell On": "What She Fell",
    "Share House": "Share House",
    "Overflow": "Overflow",
    "Click Me All Over": "Click Me All Over",
    "KAMUI": "KAMUI",
    "Demon Queen Melissa": "Demon Queen Melissa",
    "I Have No Money": "I Have No Money",
    "Lemon Angel": "Lemon Angel",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--search", action="store_true")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if args.search:
        do_search()
    if args.probe:
        do_probe()
    if args.apply:
        do_apply()
    if not (args.search or args.probe or args.apply):
        ap.print_help()


if __name__ == "__main__":
    main()

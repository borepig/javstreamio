#!/usr/bin/env python3
"""Resolve the last debrid-only hnt rows via SearXNG (video engines) +
constructible streaming-site episode pages. Writes verified direct
stream_url into titles.sqlite."""
import html
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "debrid-app", "data", "titles.sqlite")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
SEARXNG = os.environ.get("SEARXNG_URL", "http://192.168.68.83:8888")
DELAY = float(os.environ.get("HNT_DIRECT_DELAY", "0.4"))

HENTAIHAVEN = "https://hentaihaven.xxx"
HENTACITY = "https://www.hentaicity.com"
HANIME = "https://hanime.tv"

M3U8_RE = re.compile(r"(https?://[^\"'\s]+?\.m3u8[^\"'\s]*)")
MP4_RE = re.compile(r"(https?://[^\"'\s]+?\.mp4[^\"'\s]*)")
OCTOPUS_RE = re.compile(r"(https://octopusmanifest\.(?:org|com)/[0-9a-f-]+/playlist\.m3u8)")


def http_get(url, referer=None, timeout=15):
    headers = {"User-Agent": UA, "Accept": "text/html"}
    if referer:
        headers["Referer"] = referer
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) and attempt < 2:
                time.sleep(2 + attempt * 2)
                continue
            raise
        except Exception:
            if attempt < 2:
                time.sleep(1 + attempt)
                continue
            raise
    return ""


def verify(url, referer=None):
    url = html.unescape(url)
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            return True, (r.headers.get("Content-Type") or ""), r.headers.get("Content-Length")
    except Exception as e:
        return False, str(e), None


def searxng_search(query, engines, count=10):
    params = urllib.parse.urlencode({"q": query, "format": "json", "engines": engines, "count": str(count)})
    url = f"{SEARXNG}/search?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        return [x.get("url", "") for x in data.get("results", [])]
    except Exception as e:
        print(f"  SearXNG search failed '{query}' [{engines}]: {e}")
        return []


def probe(url, referer, mp4_first=False):
    try:
        page = http_get(url, referer=referer)
    except Exception as e:
        print(f"  probe failed {url}: {e}")
        return None
    if mp4_first:
        m = MP4_RE.search(page) or M3U8_RE.search(page)
    else:
        m = OCTOPUS_RE.search(page) or M3U8_RE.search(page) or MP4_RE.search(page)
    return m.group(1) if m else None


def main():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cols = [r[1] for r in cur.execute("PRAGMA table_info(titles)").fetchall()]
    if "stream_url" not in cols:
        cur.execute("ALTER TABLE titles ADD COLUMN stream_url TEXT DEFAULT ''")
        con.commit()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from enrich_lulu import english_title_from_hnt_name, extra_english_title, series_from_hnt_name

    rows = cur.execute(
        "SELECT id, name FROM titles WHERE id LIKE 'hnt:%' AND COALESCE(stream_url,'')='' AND COALESCE(lulu_token,'')=''"
    ).fetchall()
    print(f"Resolving {len(rows)} remaining hnt rows")
    updated = 0
    for rid, name in rows:
        epm = re.search(r"S\d+E(\d+)", name or "")
        ep = int(epm.group(1)) if epm else 1
        # Query titles: English title preferred, plus romanized series.
        eng = english_title_from_hnt_name(name) or extra_english_title(name)
        rom = series_from_hnt_name(name)
        titles = [t for t in [eng, rom] if t]
        # Also try hentaihaven constructible slugs per series (romanized slug).
        written = False
        # 1) Constructible hentaihaven episode pages (romanized slug, truncated).
        slug_guess = re.sub(r"\s+.*", "", rom).lower().replace(" ", "-")
        # SearXNG searches (web + video engines) across query variations to find
        # a streaming page, then probe it for a playable video.
        engines_list = ("brave,duckduckgo,google cse", "bing videos,google videos,youtube")
        domains = [
            ("hentaihaven.xxx", f"{HENTAIHAVEN}/watch/", False),
            ("hentaicity.com", HENTACITY, True),
            ("hanime.tv", HANIME, True),
            ("hentaistream.com", "https://hentaistream.com", False),
            ("hentaiera.com", "https://hentaiera.com", True),
            ("ok.ru", "https://ok.ru/", False),
        ]
        def queries_for(t):
            return [f"{t} episode {ep} hentai", f"{t} watch online", f"{t} hentai"]
        for q in titles:
            for query in queries_for(q):
                for engines in engines_list:
                    for page in searxng_search(query, engines)[:6]:
                        dom = page.split("/")[2] if page.count("/") >= 2 else ""
                        match = next(((d, r, m) for d, r, m in domains if d in dom), None)
                        if not match:
                            continue
                        _, ref, mp4first = match
                        video = probe(page, ref, mp4_first=mp4first)
                        if not video:
                            continue
                        ok, ct, _ = verify(video, referer=ref)
                        if ok:
                            cur.execute("UPDATE titles SET stream_url=? WHERE id=?", (video, rid))
                            updated += 1
                            written = True
                            print(f"  wrote {rid} ep{ep} <- {video[:90]}")
                            break
                        else:
                            print(f"  verify failed {rid}: {ct}")
                        time.sleep(DELAY)
                    if written:
                        break
                if written:
                    break
            if written:
                break
        if not written:
            print(f"  unresolved {rid} ({(name or '')[:50]} ep{ep})")
        time.sleep(DELAY)
    con.commit()
    total = cur.execute("SELECT COUNT(*) FROM titles WHERE id LIKE 'hnt:%'").fetchone()[0]
    direct = cur.execute("SELECT COUNT(*) FROM titles WHERE id LIKE 'hnt:%' AND (COALESCE(stream_url,'')!='' OR COALESCE(lulu_token,'')!='')").fetchone()[0]
    print(f"\nUpdated {updated} rows. hnt direct-playback coverage: {direct}/{total} = {direct/total*100:.1f}%")
    con.close()


if __name__ == "__main__":
    main()

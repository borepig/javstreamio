#!/usr/bin/env python3
"""Push hnt debrid-only rows toward full online-playback coverage using
direct URLs discovered via SearXNG.

Probes constructible episode pages (hentaihaven xxx) and known candidate
pages (hentaicity, hentaiera, ok.ru), extracts a directly playable video
URL (m3u8 / mp4), verifies it returns 200 + video content-type, then writes
it into titles.sqlite `stream_url` for the matching row (by series + episode).

Usage:
  python3 addon/hnt_direct_playback.py            # probe + verify + write DB
"""
import html
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
import urllib.error

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "debrid-app", "data", "titles.sqlite")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
DELAY = float(os.environ.get("HNT_DIRECT_DELAY", "0.4"))

HENTAIHAVEN = "https://hentaihaven.xxx"
OCTOPUS_RE = re.compile(r"(https://octopusmanifest\.com/[0-9a-f-]+/playlist\.m3u8)")
HENTACITY_MP4_RE = re.compile(r"(https://www\.hentaicity\.com/flv/\d+/\d+/mobile\.mp4)")
M3U8_RE = re.compile(r"(https?://[^\"'\s]+?\.m3u8[^\"'\s]*)")
MP4_RE = re.compile(r"(https?://[^\"'\s]+?\.mp4[^\"'\s]*)")


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


def verify(url, referer=None, timeout=15):
    """Return (ok, content_type, length)."""
    url = html.unescape(url)
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, (r.headers.get("Content-Type") or ""), r.headers.get("Content-Length")
    except Exception as e:
        return False, str(e), None


def probe_hentaihaven(slug, ep):
    url = f"{HENTAIHAVEN}/watch/{slug}/episode-{ep}/"
    try:
        html = http_get(url, referer=f"{HENTAIHAVEN}/watch/{slug}/")
    except Exception as e:
        print(f"  hentaihaven probe failed {slug} ep{ep}: {e}")
        return None
    m = OCTOPUS_RE.search(html)
    if m:
        return m.group(1)
    m2 = M3U8_RE.search(html)
    return m2.group(1) if m2 else None


def searxng_episode_pages(series, ep):
    """Query the local SearXNG instance for a per-episode page of `series` ep.`"""
    import urllib.parse
    SEARXNG = os.environ.get("SEARXNG_URL", "http://192.168.68.83:8888")
    query = f"{series} episode {ep} hentai"
    params = urllib.parse.urlencode({"q": query, "format": "json", "engines": "google cse,brave,duckduckgo", "count": "10"})
    url = f"{SEARXNG}/search?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            import json as _json
            data = _json.load(r)
        res = data.get("results", [])
        pages = [x.get("url", "") for x in res]
        return pages
    except Exception as e:
        print(f"  SearXNG search failed for '{series}' ep{ep}: {e}")
        return []


def probe_page(url, referer, mp4_first=True):
    try:
        html = http_get(url, referer=referer)
    except Exception as e:
        print(f"  probe failed {url}: {e}")
        return None
    if mp4_first:
        m = HENTACITY_MP4_RE.search(html) or MP4_RE.search(html)
    else:
        m = M3U8_RE.search(html) or MP4_RE.search(html)
    return m.group(1) if m else None


def ep_from_name(name):
    m = re.search(r"S\d+E(\d+)", (name or ""))
    return int(m.group(1)) if m else None


def series_fragment(name):
    n = (name or "").replace("&#39;", "'")
    n = re.sub(r"^\[[^\]]+\]\s*", "", n)
    n = re.sub(r"\s+S\d+E\d+\b.*$", "", n)
    if " - " in n:
        n = n.split(" - ")[0]
    return n.strip()


def main():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cols = [r[1] for r in cur.execute("PRAGMA table_info(titles)").fetchall()]
    if "stream_url" not in cols:
        cur.execute("ALTER TABLE titles ADD COLUMN stream_url TEXT DEFAULT ''")
        con.commit()

    # 1) Constructible hentaihaven episode pages (Fukami, Ren Arisugawa, Secret Mission).
    hnt_slugs = {
        "Yatara Yarashii Fukami-kun": ("yatara-yarashii-fukami-kun-ova", range(2, 9)),
        "Ren Arisugawa Is Actually a Girl": ("arisugawa-ren-tte-honto-wa-onn", range(2, 9)),
        "Secret Mission Undercover Agents Never Back Down": ("secret-mission-sennyuu-sousakan-wa-zettai-ni-makenai", range(3, 9)),
    }
    updated = 0
    for frag, (slug, eps) in hnt_slugs.items():
        for ep in eps:
            m3u8 = probe_hentaihaven(slug, ep)
            if not m3u8:
                continue
            ok, ct, ln = verify(m3u8, referer=f"{HENTAIHAVEN}/watch/{slug}/")
            if not ok:
                print(f"  verify failed for {frag} ep{ep}: {ct}")
                time.sleep(DELAY)
                continue
            rows = cur.execute(
                "SELECT id, name FROM titles WHERE id LIKE 'hnt:%' AND name LIKE ? AND COALESCE(stream_url,'')='' AND COALESCE(lulu_token,'')=''",
                (f"%{frag}%",)
            ).fetchall()
            for rid, name in rows:
                row_ep = ep_from_name(name)
                if row_ep == ep:
                    cur.execute("UPDATE titles SET stream_url=? WHERE id=?", (m3u8, rid))
                    updated += 1
                    print(f"  wrote {rid} ep{ep} <- {m3u8[:80]}")
            time.sleep(DELAY)

    # 2) Known candidate pages (hentaicity ep1/ep6, hentaiera, ok.ru).
    cands = []
    cj = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".searxng_stream_candidates.json")
    if os.path.exists(cj):
        cands = json.load(open(cj))
    known_pages = [c for c in cands if any(d in c["url"] for d in ["hentaicity.com", "hentaiera.com", "ok.ru"])]
    for c in known_pages:
        url = c["url"]
        if "hentaicity.com" in url:
            video = probe_page(url, "https://www.hentaicity.com/", mp4_first=True)
        elif "hentaiera.com" in url:
            video = probe_page(url, "https://hentaiera.com/", mp4_first=True)
        else:  # ok.ru
            video = probe_page(url, "https://ok.ru/", mp4_first=False)
        if not video:
            continue
        ok, ct, ln = verify(video, referer=url.split("/video")[0] if "ok.ru" in url else "https://www.hentaicity.com/")
        if not ok:
            print(f"  verify failed for {c['series']}: {ct}")
            continue
        # Map by series fragment + episode.
        frag = c["series"]
        ep = None
        m = re.search(r"episode-(\d+)", url) or re.search(r"-together-(\d+)-", url)
        if m:
            ep = int(m.group(1))
        rows = cur.execute(
            "SELECT id, name FROM titles WHERE id LIKE 'hnt:%' AND COALESCE(stream_url,'')='' AND COALESCE(lulu_token,'')=''"
        ).fetchall()
        for rid, name in rows:
            sfrag = series_fragment(name)
            if frag.lower() in sfrag.lower() or sfrag.lower() in frag.lower():
                row_ep = ep_from_name(name)
                if ep is None or row_ep is None or row_ep == ep:
                    cur.execute("UPDATE titles SET stream_url=? WHERE id=?", (video, rid))
                    updated += 1
                    print(f"  wrote {rid} <- {video[:80]}")
        time.sleep(DELAY)

    # 3) For every remaining debrid-only hnt row, ask SearXNG for a per-episode
    #    page, probe it for a playable video, verify, and write stream_url.
    remaining = cur.execute(
        "SELECT id, name FROM titles WHERE id LIKE 'hnt:%' AND COALESCE(stream_url,'')='' AND COALESCE(lulu_token,'')=''"
    ).fetchall()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from enrich_lulu import english_title_from_hnt_name, extra_english_title
        _HAS_LULU = True
    except Exception:
        _HAS_LULU = False

    for rid, name in remaining:
        frag = series_fragment(name)
        row_ep = ep_from_name(name)
        if not frag:
            continue
        # Prefer the English title (matches streaming-site titles) if present.
        query_title = frag
        if _HAS_LULU:
            eng = english_title_from_hnt_name(name) or extra_english_title(name)
            if eng:
                query_title = eng
        pages = searxng_episode_pages(query_title, row_ep if row_ep else 1)
        written = False
        for page in pages[:6]:
            dom = page.split("/")[2] if len(page.split("/")) > 2 else ""
            if "hentaihaven.xxx" in dom:
                video = probe_page(page, f"https://hentaihaven.xxx/watch/", mp4_first=False)
                ref = f"https://hentaihaven.xxx/watch/"
            elif "hentaicity.com" in dom:
                video = probe_page(page, "https://www.hentaicity.com/", mp4_first=True)
                ref = "https://www.hentaicity.com/"
            elif "hentaiera.com" in dom:
                video = probe_page(page, "https://hentaiera.com/", mp4_first=True)
                ref = "https://hentaiera.com/"
            elif "ok.ru" in dom:
                video = probe_page(page, "https://ok.ru/", mp4_first=False)
                ref = "https://ok.ru/"
            else:
                continue
            if not video:
                continue
            ok, ct, ln = verify(video, referer=ref)
            if not ok:
                continue
            cur.execute("UPDATE titles SET stream_url=? WHERE id=?", (video, rid))
            updated += 1
            written = True
            print(f"  wrote {rid} ep{row_ep} <- {video[:90]}")
            break
        time.sleep(DELAY)
        if not written:
            print(f"  no playable video found for {rid} ({frag} ep{row_ep})")

    con.commit()
    # Final coverage
    total = cur.execute("SELECT COUNT(*) FROM titles WHERE id LIKE 'hnt:%'").fetchone()[0]
    direct = cur.execute("SELECT COUNT(*) FROM titles WHERE id LIKE 'hnt:%' AND (COALESCE(stream_url,'')!='' OR COALESCE(lulu_token,'')!='')").fetchone()[0]
    print(f"\nUpdated {updated} rows. hnt direct-playback coverage: {direct}/{total} = {direct/total*100:.1f}%")
    con.close()


if __name__ == "__main__":
    main()

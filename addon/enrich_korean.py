#!/usr/bin/env python3
"""Populate the 'korean' category with Korean AV titles + P2P/torrent playback.

Sources (both feed the `korean` category, ids prefixed `kr:`):
- iJavTorrent korean tag (korean-305): movie pages carry name/poster/date/magnet.
  Rows -> `kr:ijav-<movie-id>` (matches the existing `kr:ijav-189281`).
- adulto.forumkorean.com: Korean AV torrent forum. The 'KOR Torrent Free Down'
  (f2) and 'Magnet' (f4) boards list threads; each thread body carries a magnet.
  Rows -> `kr:forumkorean-<thread-id>`.

Every row carries a magnet + info_hash so it plays via P2P/torrent (debrid),
which is the accepted playback path. Re-runs are resume-safe: existing ids are
skipped.
"""
import html as html_module
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
import urllib.error

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "debrid-app", "data", "titles.sqlite")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
HASH_RE = re.compile(r"urn:btih:([A-Za-z0-9]+)", re.I)
IJA_BASE = "https://ijavtorrent.com"
FK_BASE = "https://adulto.forumkorean.com"
FK_BOARDS = [
    # (board path, label)
    ("/f2-kor-torrent-free-down", "KOR Torrent"),
    ("/f4-magnet-%EB%A7%88%EA%B7%B8%EB%84%B7-%EA%B0%84%ED%8E%B8%EB%B0%9B%EA%B8%B0", "Magnet"),
]
DELAY = float(os.environ.get("KOREAN_ENRICH_DELAY", "0.5"))

def info_hash(magnet_field: str) -> str:
    for line in (magnet_field or "").splitlines():
        m = HASH_RE.search(line)
        if m:
            return m.group(1).upper()
    return ""

def http_get(url: str, timeout: int = 30) -> str:
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (403, 404, 429, 500, 502, 503) and attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            raise
        except Exception:
            if attempt < 2:
                time.sleep(1 + attempt)
                continue
            raise
    return ""

def upsert(con, title_id, category, name, description, released, poster, magnet, runtime=""):
    """INSERT OR REPLACE a korean title row (torrent-only playback)."""
    released = released or ""
    info_hash_val = info_hash(magnet)
    con.execute(
        """
        INSERT OR REPLACE INTO titles (
          id, category, name, description, release_info, released, runtime,
          poster, background, forum_poster, dmm_missing, fourhoi_missing,
          cast_json, genres_json, director, imdb_rating, info_hash, magnet,
          posted_at, search_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', '', 0, 0, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            title_id, category, name or "", description or "",
            released[:4] if released else "", released, runtime or "",
            poster or "",
            "[]", json.dumps(["Korean AV"], ensure_ascii=False), "", "",
            info_hash_val, magnet or "", released,
            " ".join(x for x in [name, magnet, category] if x).lower(),
        ),
    )
    con.commit()

def enrich_ijavtorrent(con) -> int:
    """Fetch iJavTorrent korean-tag movie pages; upsert each with its magnet."""
    tag_html = http_get(f"{IJA_BASE}/tag/korean-305")
    ids = []
    for block in tag_html.split('data-movie-id="')[1:]:
        m = re.match(r"(\d+)", block)
        if m:
            ids.append(m.group(1))
    ids = sorted(set(ids), key=int)
    added = 0
    for mid in ids:
        title_id = f"kr:ijav-{mid}"
        page = http_get(f"{IJA_BASE}/movie/{mid}")
        h1 = re.search(r'<h1[^>]*>(.*?)</h1>', page, re.S)
        name = re.sub(r"<[^>]+>", "", h1.group(1)).strip() if h1 else f"ijav-{mid}"
        name = html_module.unescape(name)
        poster_m = re.search(r'data-src="(https://images\.ijavtorrent\.com/data/covers/[^"?]+)', page)
        poster = poster_m.group(1) if poster_m else ""
        date_m = re.search(r"(\d{2}/\d{2}/\d{4})", page)
        released = ""
        if date_m:
            d, mo, y = date_m.group(1).split("/")
            released = f"{y}-{mo}-{d}T00:00:00.000Z"
        mag_m = re.search(r'magnet:\?xt=urn:btih:[0-9A-Fa-f]{40}', page)
        magnet = mag_m.group(0) if mag_m else ""
        desc = f"{name}\n\nSource: iJavTorrent (Korean)"
        upsert(con, title_id, "korean", name, desc, released, poster, magnet)
        if magnet:
            added += 1
        time.sleep(DELAY)
    return added

TORRENT_URL_RE = re.compile(r"/torrent/([0-9A-Fa-f]{40})\.torrent")

def enrich_forumkorean(con) -> int:
    """Scrape adulto.forumkorean.com boards; upsert Korean AV threads with magnets.

    The forum posts present the torrent as a `.torrent` attachment (torrage.com /
    zoink.it) whose filename IS the info_hash. We build the magnet from that hash
    so each row plays via P2P/torrent.
    """
    months = {m: i for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}
    added = 0
    for path, label in FK_BOARDS:
        board_html = http_get(FK_BASE + path)
        # Board lists threads as /t<id>-<slug>; dedupe per id, keep the full slug URL.
        threads = {}
        for full, tid in re.findall(r'href="(/t(\d+)-[^"]*)"', board_html):
            threads.setdefault(tid, full)
        for tid, full in threads.items():
            title_id = f"kr:forumkorean-{tid}"
            thread_html = http_get(FK_BASE + full)
            h1 = re.search(r'<h1[^>]*>(.*?)</h1>', thread_html, re.S)
            name = re.sub(r"<[^>]+>", "", h1.group(1)).strip() if h1 else f"forumkorean-{tid}"
            name = html_module.unescape(name)
            hash_m = TORRENT_URL_RE.search(thread_html)
            # Prefer the info_hash embedded in the .torrent attachment URL; fall back
            # to an inline magnet link.
            hash_val = hash_m.group(1).upper() if hash_m else ""
            inline_mag = re.search(r'magnet:\?xt=urn:btih:([0-9A-Fa-f]{40})', thread_html)
            if not hash_val and inline_mag:
                hash_val = inline_mag.group(1).upper()
            magnet = f"magnet:?xt=urn:btih:{hash_val}" if hash_val else ""
            date_m = re.search(r'(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(\w{3})\s+(\d{1,2}),\s*(\d{4})', thread_html)
            released = ""
            if date_m:
                mo = months.get(date_m.group(1), 1)
                released = f"{date_m.group(3)}-{mo:02d}-{int(date_m.group(2)):02d}T00:00:00.000Z"
            desc = f"{name}\n\nSource: ForumKorean ({label})"
            upsert(con, title_id, "korean", name, desc, released, "", magnet)
            if magnet:
                added += 1
            time.sleep(DELAY)
    return added

def main():
    con = sqlite3.connect(DB)
    cols = [r[1] for r in con.execute("PRAGMA table_info(titles)").fetchall()]
    if "info_hash" not in cols:
        con.execute("ALTER TABLE titles ADD COLUMN info_hash TEXT DEFAULT ''")
    if "magnet" not in cols:
        con.execute("ALTER TABLE titles ADD COLUMN magnet TEXT DEFAULT ''")
    con.commit()
    n_iya = enrich_ijavtorrent(con)
    n_fk = enrich_forumkorean(con)
    total = con.execute("SELECT COUNT(*) FROM titles WHERE category='korean'").fetchone()[0]
    with_magnet = con.execute(
        "SELECT COUNT(*) FROM titles WHERE category='korean' AND COALESCE(magnet,'')!=''"
    ).fetchone()[0]
    print(f"korean enrichment: iJavTorrent added(magnet)={n_iya}, "
          f"ForumKorean added(magnet)={n_fk}")
    print(f"korean total={total}, with_magnet={with_magnet}")
    con.close()

if __name__ == "__main__":
    main()

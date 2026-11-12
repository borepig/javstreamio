#!/usr/bin/env python3
"""HK Cat III AV magnet search (Wikipedia-derived meta).

For each of the 172 AV titles in addon/hk_cat3_av.json, search SearXNG
(hongkong meta: zh name + year), fetch the top result pages, extract
magnet links, and score candidates by the user's priorities:

  - high bitrate / high resolution (1080p/2160p/4K, Bluray/REMUX/高清/HD)
  - original voice = Cantonese (粤語 / cantonese / 原聲)
  - dubbing (國語/eng dub/雙語) is penalised

Winning magnets are written to the live SQLite as category 'hongkong'
rows (id 'hk:cat3-<idx>'). Resume-safe: rows that already have a magnet
are skipped.

Usage:
    python3 addon/hk_cat3_magnet_search.py [--limit N] [--delay SEC] [--dry-run]
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
META_JSON = ROOT / "addon" / "hk_cat3_av.json"
DB = ROOT / "debrid-app" / "data" / "titles.sqlite"

SEARXNG = os.environ.get("SEARXNG_URL", "http://192.168.68.83:8888")

MAGNET_RE = re.compile(r'magnet:\?xt=urn:btih:([A-Fa-f0-9]{40})')
HASH_RE = re.compile(r'urn:btih:([A-Fa-f0-9]{40})')

# User priority markers (case-insensitive).
HI_RES_RE = re.compile(
    r"1080p|2160p|2160|4k|bluray|blu-ray|remux|x264|x265|hevc|avc|高清|hd\b", re.I)
CANTONESE_RE = re.compile(r"粤語|粤语|cantonese|原聲|原音|original\s*audio", re.I)
DUB_RE = re.compile(r"國語|国语|mandarin|eng\s*dub|english\s*dub|雙語|双语|dubbed|雙語版|國語版", re.I)
YEAR_RE = re.compile(r"(\d{4})")


def search_sxng(query: str, num: int = 10) -> list[dict]:
    """One SearXNG JSON search. Returns results (url/title/content)."""
    url = f"{SEARXNG}/search?q={quote(query)}&format=json&pageno=1"
    backoff = 15
    while True:
        try:
            req = Request(url, headers={"User-Agent": "javstreamio-hkcat3/1.0"})
            with urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
            return data.get("results") or []
        except Exception as e:
            print(f"    sxng error: {e}; retry in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)


def fetch_page_magnets(url: str) -> list[tuple[str, str]]:
    """Fetch a page, return [(magnet, hash)] found in the HTML."""
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (javstreamio)"})
        with urlopen(req, timeout=15) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception:
        return []
    magnets = []
    for m in MAGNET_RE.finditer(html):
        full = m.group(0)
        magnets.append((full, m.group(1)))
    if not magnets:
        for m in HASH_RE.finditer(html):
            h = m.group(1)
            magnets.append((f"magnet:?xt=urn:btih:{h}", h))
    return magnets


def build_query(t: dict) -> str:
    year = t.get("year", "")
    if t.get("en"):
        base = f"{t['en']} {year}"
    else:
        base = f"{t['zh']} {year}"
    # user priorities: hi-res + Cantonese original audio
    return f"{base} 1080p 粤語"


def score_candidate(name: str, t: dict) -> int:
    text = name
    s = 0
    if HI_RES_RE.search(text):
        s += 3
    if CANTONESE_RE.search(text):
        s += 3
    if DUB_RE.search(text):
        s -= 3
    # relevance: candidate name mentions the title's zh/en name
    names = [t.get("zh", ""), t.get("en", "")]
    for n in names:
        if n and n.lower() in text.lower():
            s += 1
            break
    return s


def open_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB))
    db.execute("PRAGMA journal_mode=WAL")
    return db


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    meta = json.loads(META_JSON.read_text(encoding="utf-8"))
    titles = meta["av"]
    total = len(titles)
    if args.limit:
        titles = titles[:args.limit]

    db = open_db()
    found = skipped = nomag = failed = 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    for idx, t in enumerate(titles):
        slug = f"hk:cat3-{idx}"
        row = db.execute("SELECT magnet FROM titles WHERE id=?", (slug,)).fetchone()
        if row and row[0]:
            skipped += 1
            print(f"[hkcat3] {idx + 1}/{total} {t['zh']} ({t.get('year','')}) — already has magnet, skip")
            continue

        zh = t.get("zh", "")
        query = build_query(t)
        results = search_sxng(query)
        best = None  # (score, magnet, hash, source)
        for res in results[:8]:
            url = (res.get("url") or "").strip()
            if not url:
                continue
            for magnet, h in fetch_page_magnets(url):
                cand_name = f"{res.get('title','')} {res.get('content','')}"
                s = score_candidate(cand_name, t)
                if best is None or s > best[0]:
                    best = (s, magnet, h, url)
            time.sleep(0.3)

        if best:
            found += 1
            zh, en = t.get("zh", ""), t.get("en", "")
            name = en or zh
            desc_parts = [zh]
            if en:
                desc_parts.append(en)
            desc_parts.append(f"(Wikipedia 香港三级片列表 {t.get('year','')})")
            released = f"{t.get('year','2000')}-01-01T00:00:00.000Z"
            search_text = " ".join(x for x in [zh, en, t.get("year", "")] if x).lower()
            if not args.dry_run:
                db.execute(
                    """INSERT INTO titles (id, name, description, released,
                                         magnet, info_hash, posted_at, search_text, category, meta_rank)
                       VALUES (?,?,?,?,?,?,?, ?, 'hongkong', 0)
                       ON CONFLICT(id) DO UPDATE SET
                         description=excluded.description,
                         released=excluded.released,
                         magnet=excluded.magnet,
                         info_hash=excluded.info_hash,
                         posted_at=excluded.posted_at,
                         search_text=excluded.search_text,
                         meta_rank=0
                    """,
                    (slug, name, " ".join(desc_parts), released, best[1],
                     best[2], now, search_text),
                )
            db.commit()
            print(f"[hkcat3] {idx + 1}/{total} {zh} ({t.get('year','')}) -> magnet score={best[0]} src={best[3]}")
        else:
            nomag += 1
            print(f"[hkcat3] {idx + 1}/{total} {zh} ({t.get('year','')}) -> no magnet found")
        if args.delay:
            time.sleep(args.delay)

    db.close()
    print(f"done: found={found} skipped={skipped} no_magnet={nomag} failed={failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

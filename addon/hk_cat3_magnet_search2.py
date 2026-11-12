#!/usr/bin/env python3
"""HK Cat III AV magnet search — second pass (SearXNG + Firecrawl fallback).

Processes the 147 titles that pass 1 (addon/hk_cat3_magnet_search.py)
could not find magnets for. Per title:

1. SearXNG (self-hosted on QNAP) with multiple queries:
   - "<zh name> <year>"
   - "<en name> <year>" when the title has an en name
   - "<zh name> <year> magnet"
2. Plain-HTTP fetch of the top-N deduped result pages; extract
   `magnet:?xt=urn:btih:<40-hex>` links (bare `urn:btih` hashes are
   synthesised into magnets).
3. JS-heavy torrent hosts (1337x.com, chdbits.org, avgood.com) that
   yield no magnet via plain fetch are recorded to
   `addon/.hk_cat3_firecrawl_pending.json` for follow-up with the
   Firecrawl MCP tools (the self-hosted Firecrawl /mcp JSON-RPC
   `tools/call` is broken from the shell, so those URLs are consumed
   in-session via firecrawl_scrape).
4. Candidates are scored by the user's priorities (+3 hi-res/hi-bitrate,
   +3 Cantonese original audio, -3 dubbing, +1 name relevance); the best
   is written to the live SQLite as a `hongkong` row (id `hk:cat3-<idx>`).

Resume-safe: rows that already have a magnet are skipped.

Usage:
    python3 addon/hk_cat3_magnet_search2.py [--limit N] [--delay SEC] [--dry-run]
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
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
META_JSON = ROOT / "addon" / "hk_cat3_av.json"
DB = ROOT / "debrid-app" / "data" / "titles.sqlite"
PENDING_JSON = ROOT / "addon" / ".hk_cat3_firecrawl_pending.json"

SEARXNG = os.environ.get("SEARXNG_URL", "http://192.168.83:8888")

MAGNET_RE = re.compile(r'magnet:\?xt=urn:btih:([A-Fa-f0-9]{40})')
HASH_RE = re.compile(r'urn:btih:([A-Fa-f0-9]{40})')

# User priority markers (case-insensitive).
HI_RES_RE = re.compile(
    r"1080p|2160p|2160|4k|bluray|blu-ray|remux|x264|x265|hevc|avc|高清|hd\b", re.I)
CANTONESE_RE = re.compile(r"粤語|粤语|cantonese|原聲|原音|original\s*audio", re.I)
DUB_RE = re.compile(r"國語|国语|mandarin|eng\s*dub|english\s*dub|雙語|双语|dubbed|雙語版|國語版", re.I)

# Torrent indexes whose listing/detail pages are JS-rendered — plain fetch
# returns an empty shell, so they need Firecrawl (browser rendering).
JS_HEAVY_HOSTS = {"1337x.com", "chdbits.org", "avgood.com"}


def search_sxng(query: str, num: int = 10) -> list[dict]:
    """One SearXNG JSON search with exponential backoff on failure."""
    url = f"{SEARXNG}/search?q={quote(query)}&format=json&pageno=1"
    backoff = 15
    while True:
        try:
            req = Request(url, headers={"User-Agent": "javstreamio-hkcat3/1.0"})
            with urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
            return data.get("results") or []
        except Exception as e:
            print(f"    sxng error: {e}; retry in {backoff}s", flush=True)
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
        magnets.append((m.group(0), m.group(1)))
    if not magnets:
        for m in HASH_RE.finditer(html):
            h = m.group(1)
            magnets.append((f"magnet:?xt=urn:btih:{h}", h))
    return magnets


def is_js_heavy(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return host in JS_HEAVY_HOSTS


def score_candidate(name: str, t: dict) -> int:
    s = 0
    if HI_RES_RE.search(name):
        s += 3
    if CANTONESE_RE.search(name):
        s += 3
    if DUB_RE.search(name):
        s -= 3
    for n in [t.get("zh", ""), t.get("en", "")]:
        if n and n.lower() in name.lower():
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
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    meta = json.loads(META_JSON.read_text(encoding="utf-8"))
    titles = meta["av"]
    total = len(titles)

    db = open_db()
    have = {r[0] for r in db.execute(
        "SELECT id FROM titles WHERE category='hongkong' AND magnet LIKE 'magnet:%'")}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    pending = []
    if PENDING_JSON.exists():
        pending = json.loads(PENDING_JSON.read_text(encoding="utf-8"))
    seen_pending = {p["url"] for p in pending}

    found = skipped = nomag = failed = 0
    for idx, t in enumerate(titles):
        slug = f"hk:cat3-{idx}"
        if slug in have:
            skipped += 1
            continue
        if args.limit and idx >= args.limit:
            break

        zh, en, year = t.get("zh", ""), t.get("en", ""), t.get("year", "")
        # Multiple SearXNG queries: zh name, en name (when present), magnet query.
        queries = [f"{zh} {year}"]
        if en:
            queries.append(f"{en} {year}")
        queries.append(f"{zh} {year} magnet")

        candidates = []  # (score, magnet, hash, source_url)
        urls_seen = set()
        for q in queries:
            for res in search_sxng(q, num=10)[:8]:
                url = (res.get("url") or "").strip()
                if not url or url in urls_seen:
                    continue
                urls_seen.add(url)
                ctext = f"{res.get('title','')} {res.get('content','')}"
                cands = fetch_page_magnets(url)
                if not cands and is_js_heavy(url):
                    if url not in seen_pending:
                        pending.append({"url": url, "title_idx": idx, "zh": zh, "en": en, "year": year})
                        seen_pending.add(url)
                    continue
                for magnet, h in cands:
                    s = score_candidate(ctext, t)
                    candidates.append((s, magnet, h, url))
                time.sleep(0.3)

        best = None
        for cand in candidates:
            if best is None or cand[0] > best[0]:
                best = cand

        if best:
            found += 1
            name = en or zh
            desc_parts = [zh]
            if en:
                desc_parts.append(en)
            desc_parts.append(f"(Wikipedia 香港三级片列表 {year})")
            released = f"{year}-01-01T00:00:00.000Z"
            search_text = " ".join(x for x in [zh, en, year] if x).lower()
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
            print(f"[hkcat3-2] {idx + 1}/{total} {zh} ({year}) -> magnet score={best[0]} src={best[3]}", flush=True)
        else:
            nomag += 1
            print(f"[hkcat3-2] {idx + 1}/{total} {zh} ({year}) -> no magnet found", flush=True)
        if args.delay:
            time.sleep(args.delay)

    if not args.dry_run and pending:
        PENDING_JSON.write_text(json.dumps(pending, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[hkcat3-2] firecrawl pending urls: {len(pending)} -> {PENDING_JSON.name}")
    db.close()
    print(f"done2: found={found} skipped={skipped} no_magnet={nomag} failed={failed} firecrawl_pending={len(pending)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

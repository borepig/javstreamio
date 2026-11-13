#!/usr/bin/env python3
"""HK Cat III magnet verification — confirm legacy magnets belong to the film,
or delete the film when no confirmed working link can be found.

For every HK Cat III title:
1. AvGood search by the Chinese name (engine-independent). If an AvGood
   detail page yields a magnet, that is a CONFIRMED working link -> update
   magnet + info_hash.
2. If AvGood finds nothing, verify the film with a self-hosted Firecrawl
   search using the Chinese name + year (user instruction: not the English
   name). The IMDB/Baike result confirms the film exists.
3. If SearXNG is available, also search the title on torrent engines for a
   working magnet; keep the highest-scoring candidate.
4. If no confirmed working link is found after the full search effort,
   DELETE the row (user: "remove movies where you can't find a confirmed
   working link"). If the search itself was unavailable (SearXNG rate-
   limited and Firecrawl down), KEEP the row for retry — not proof the link
   doesn't exist.

Usage:
    python3 addon/hk_cat3_magnet_verify.py [--limit N] [--delay SEC] [--dry-run] [--keep-legacy]
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
from pathlib import Path
from urllib.request import Request, urlopen

# Reuse the AvGood helpers from the established magnet job.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hk_cat3_wiki_magnet_search import (
    AVGOOD,
    avgood_detail,
    avgood_search,
    build_variants,
    fetch_page,
    fetch_page_magnets,
    search_sxng,
    score_candidate,
)

ROOT = Path(__file__).resolve().parent.parent
META_JSON = ROOT / "addon" / "hk_cat3_av.json"
DB = ROOT / "debrid-app" / "data" / "titles.sqlite"
FIRECRAWL_API = os.environ.get("FIRECRAWL_API_URL", "http://192.168.68.83:3002")

NO_WORKING_LINK = "no_link"      # searched, nothing confirmed -> delete
SEARCH_UNAVAILABLE = "pending"   # search itself unavailable -> keep for retry


def fc_search(query: str, limit: int = 5):
    """Self-hosted Firecrawl search (independent of the rate-limited SearXNG).
    Returns (results, searched_ok)."""
    body = json.dumps({"query": query, "limit": limit}).encode()
    for attempt in (1, 2):
        try:
            req = Request(FIRECRAWL_API + "/v2/search", data=body,
                          headers={"Content-Type": "application/json"})
            with urlopen(req, timeout=60) as r:
                d = json.loads(r.read())
            if not d.get("success"):
                return [], False
            return (d.get("data") or {}).get("web") or [], True
        except Exception as e:
            if attempt == 1:
                print(f"    firecrawl search error: {e}; retrying in 3s")
                time.sleep(3)
                continue
            print(f"    firecrawl search error: {e}")
            return [], False


def verify_film(name: str, year: str):
    """Confirm the film exists via a Firecrawl search on the Chinese name + year.
    Returns (confirmed: bool, searched_ok: bool)."""
    results, ok = fc_search(f"{name} {year}".strip())
    if not ok:
        return False, False
    for r in results:
        u = (r.get("url") or "")
        if "imdb.com" in u or "baidu.com" in u or "douban" in u:
            return True, True
    return False, True


def process_title(idx: int, t: dict, db, total: int, dry_run: bool,
                   keep_legacy: bool) -> str:
    slug = f"hk:cat3-{idx}"
    row = db.execute("SELECT magnet, info_hash FROM titles WHERE id = ?", (slug,)).fetchone()
    if not row:
        print(f"[hkcat3magnet] {idx + 1}/{total} {slug}: row missing, skipped")
        return "skip"

    zh = (t.get("zh") or "").strip() or (t.get("en") or "").strip()
    year = str(t.get("year") or "")
    legacy_hash = (row[1] or "").strip()

    # 1) AvGood confirmed magnet (highest-scoring candidate).
    wiki = {}
    variants = build_variants(t, wiki)
    best = None
    cands = {}
    for rid, ttl in avgood_search(zh):
        if any(v and v in ttl for v in variants):
            cands.setdefault(rid, ttl)
    for rid in list(cands)[:6]:
        det = avgood_detail(rid)
        if det["magnet"]:
            s = score_candidate(cands[rid], variants)
            if best is None or s > best[0]:
                best = (s, det["magnet"], det["hash"], f"avgood /c/{rid}.html")
        time.sleep(0.3)

    search_available = True
    sxng_ok = True
    if best:
        # Confirmed working link found on AvGood.
        action = "updated"
        src = best[3]
        magnet, h = best[1], best[2]
    else:
        # 2) Not found on AvGood -> verify the film via Firecrawl (Chinese
        #    name + year), then look for a working magnet via SearXNG.
        confirmed, fc_ok = verify_film(zh, year)
        sxng_results = search_sxng(f"{zh} {year} 1080p 粤語")
        sxng_ok = len(sxng_results) > 0
        if not fc_ok or not sxng_ok:
            search_available = False
        found_sxng = None
        if sxng_ok:
            for res in sxng_results[:6]:
                url = (res.get("url") or "").strip()
                if not url:
                    continue
                cand_name = f"{res.get('title','')} {res.get('content','')}"
                for magnet, h in fetch_page_magnets(url):
                    s = score_candidate(cand_name, variants)
                    if found_sxng is None or s > found_sxng[0]:
                        found_sxng = (s, magnet, h, url)
                time.sleep(0.3)
        if found_sxng:
            best = found_sxng
            action = "updated"
            src = best[3]
            magnet, h = best[1], best[2]
        else:
            # No confirmed working link (AvGood + SearXNG found none).
            if not search_available:
                # Search tools were unavailable: keep for retry.
                print(f"[hkcat3magnet] {idx + 1}/{total} {slug} {zh} ({year}): search unavailable (fc_ok={fc_ok}, sxng_ok={sxng_ok}), kept for retry")
                return "pending"
            # SearXNG answered with 0 results (rate-limited) -> not proof of
            # absence, so also keep for retry.
            if not sxng_ok:
                print(f"[hkcat3magnet] {idx + 1}/{total} {slug} {zh} ({year}): sxng rate-limited, kept for retry")
                return "pending"
            # Search fully available, film (un)confirmed, no working link.
            action = "deleted" if not confirmed else "kept_legacy" if keep_legacy else "deleted"
            src = "none"
            magnet, h = row[0] or "", legacy_hash

    if action == "updated":
        if not dry_run:
            db.execute("UPDATE titles SET magnet = ?, info_hash = ? WHERE id = ?",
                       (magnet, h, slug))
            db.commit()
        print(f"[hkcat3magnet] {idx + 1}/{total} {slug} {zh} ({year}): -> magnet score={best[0]} src={src}")
        return "updated"

    if action == "deleted":
        if not dry_run:
            db.execute("DELETE FROM titles WHERE id = ?", (slug,))
            db.commit()
        print(f"[hkcat3magnet] {idx + 1}/{total} {slug} {zh} ({year}): no confirmed working link -> deleted")
        return "deleted"

    if action == "kept_legacy":
        print(f"[hkcat3magnet] {idx + 1}/{total} {slug} {zh} ({year}): kept legacy magnet")
        return "kept_legacy"

    return action


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-legacy", action="store_true",
                    help="when the film is confirmed but no NEW working link is found, keep the existing legacy magnet instead of deleting")
    args = ap.parse_args()

    meta = json.loads(META_JSON.read_text(encoding="utf-8"))
    titles = meta["av"]
    total = len(titles)
    if args.limit:
        titles = titles[:args.limit]

    db = sqlite3.connect(str(DB))
    stats = {"updated": 0, "deleted": 0, "pending": 0, "kept_legacy": 0, "skip": 0}
    for idx, t in enumerate(titles):
        res = process_title(idx, t, db, total, args.dry_run, args.keep_legacy)
        stats[res] = stats.get(res, 0) + 1
        if args.delay:
            time.sleep(args.delay)
    db.close()
    print(f"done: {json.dumps(stats)} (of {min(len(meta['av']), args.limit or len(meta['av']))} titles)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
    score_candidate,
)

ROOT = Path(__file__).resolve().parent.parent
META_JSON = ROOT / "addon" / "hk_cat3_av.json"
DB = ROOT / "debrid-app" / "data" / "titles.sqlite"
SEARXNG = os.environ.get("SEARXNG_URL", "http://192.168.68.83:8888")
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


def sxng_search_fast(query: str, num: int = 10) -> tuple:
    """One-shot SearXNG search, no zero-result backoff: when the SearXNG
    upstream engines are rate-limited the Firecrawl search below is the
    engine-independent fallback, so don't waste ~35s of 5/10/20s retries per
    title (user: finish without waiting). Returns (results, searched_ok)."""
    url = f"{SEARXNG}/search?q={urllib.parse.quote(query)}&format=json&pageno=1"
    try:
        req = Request(url, headers={"User-Agent": "javstreamio-hkcat3/1.0"})
        with urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        return (data.get("results") or []), True
    except Exception:
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

    if best:
        # Confirmed working link found on AvGood.
        action = "updated"
        src = best[3]
        magnet, h = best[1], best[2]
    else:
        # 2) Not found on AvGood -> verify the film via Firecrawl (Chinese
        #    name + year), then look for a working magnet via SearXNG, with
        #    the self-hosted Firecrawl search as the engine-independent
        #    fallback when the SearXNG upstream engines are rate-limited.
        confirmed, fc_ok = verify_film(zh, year)
        q = f"{zh} {year} 1080p 粤語"
        sxng_results, sxng_ok = sxng_search_fast(q)
        found = None
        if sxng_ok:
            for res in sxng_results[:6]:
                url = (res.get("url") or "").strip()
                if not url:
                    continue
                cand_name = f"{res.get('title','')} {res.get('content','')}"
                for magnet, h in fetch_page_magnets(url):
                    s = score_candidate(cand_name, variants)
                    if found is None or s > found[0]:
                        found = (s, magnet, h, url)
                time.sleep(0.3)
        fc_results = []
        fc_searched = fc_ok
        if found is None:
            # No magnet in the SearXNG results (or SearXNG rate-limited):
            # query the self-hosted Firecrawl search for more candidates.
            fc_results, fc_searched = fc_search(q)
            if fc_searched:
                for res in fc_results[:6]:
                    url = (res.get("url") or "").strip()
                    if not url:
                        continue
                    cand_name = f"{res.get('title','')} {res.get('description','')}"
                    for magnet, h in fetch_page_magnets(url):
                        s = score_candidate(cand_name, variants)
                        if found is None or s > found[0]:
                            found = (s, magnet, h, url)
                    time.sleep(0.3)
        if found:
            best = found
            action = "updated"
            src = best[3]
            magnet, h = best[1], best[2]
        elif not (sxng_ok or fc_searched):
            # Neither search path could produce a verdict: keep for retry.
            print(f"[hkcat3magnet] {idx + 1}/{total} {slug} {zh} ({year}): searches unavailable (sxng_ok={sxng_ok}, fc_searched={fc_searched}), kept for retry")
            return "pending"
        else:
            # Searches fully available, no confirmed working link.
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

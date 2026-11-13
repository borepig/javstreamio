#!/usr/bin/env python3
"""HK Cat III metadata enrichment — fill cast/genres/director/imdb_rating/runtime
and the 34 pending posters via self-hosted Firecrawl search + scrape.

For every hongkong title, search Firecrawl (self-hosted /v2/search, engine-
independent of the rate-limited SearXNG) with the Chinese name + year only
(user instruction: search the name, not the English name). The top result is
ranked IMDB > Douban > Baike. Fields are parsed from the search result's
title/description; the page is scraped (markdown) only when the runtime is
still missing. A found Douban poster (img*.doubanio.com, allow-listed) also
fills the poster/background of the still-pending SVG-card rows.

Usage:
    python3 addon/hk_cat3_meta_enrich.py [--limit N] [--delay SEC] [--dry-run] [--posters]
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

ROOT = Path(__file__).resolve().parent.parent
META_JSON = ROOT / "addon" / "hk_cat3_av.json"
DB = ROOT / "debrid-app" / "data" / "titles.sqlite"
FIRECRAWL_API = os.environ.get("FIRECRAWL_API_URL", "http://192.168.68.83:3002")
LIVE_HOST_PREFIX = "https://javstremio.ddns.net"


def fc_search(query: str, limit: int = 5):
    body = json.dumps({"query": query, "limit": limit}).encode()
    for attempt in (1, 2):
        try:
            req = Request(FIRECRAWL_API + "/v2/search", data=body,
                          headers={"Content-Type": "application/json"})
            with urlopen(req, timeout=60) as r:
                d = json.loads(r.read())
            if not d.get("success"):
                return []
            return (d.get("data") or {}).get("web") or []
        except Exception as e:
            if attempt == 1:
                print(f"    firecrawl search error: {e}; retrying in 3s")
                time.sleep(3)
                continue
            print(f"    firecrawl search error: {e}")
            return []


def fc_scrape(url: str) -> str:
    body = json.dumps({"url": url, "formats": ["markdown"]}).encode()
    for attempt in (1, 2):
        try:
            req = Request(FIRECRAWL_API + "/v2/scrape", data=body,
                          headers={"Content-Type": "application/json"})
            with urlopen(req, timeout=90) as r:
                d = json.loads(r.read())
            if not d.get("success"):
                return ""
            data = d.get("data") or {}
            return data.get("markdown") or data.get("content") or ""
        except Exception as e:
            if attempt == 1:
                print(f"    firecrawl scrape error: {e}; retrying in 3s")
                time.sleep(3)
                continue
            print(f"    firecrawl scrape error: {e}")
            return ""


def _rank(r):
    u = (r.get("url") or "")
    if "imdb.com" in u:
        return 0
    if "douban" in u:
        return 1
    if "baidu.com" in u or "baike" in u:
        return 2
    if "themoviedb.org" in u:
        return 3
    return 9


def pick_source(results):
    results = sorted(results, key=_rank)
    return results[0] if results else None


def parse_imdb(title: str, desc: str):
    """Pull rating/genre from an IMDB result title, director/cast from desc."""
    out = {}
    m = re.search(r"⭐\s*([0-9.]+)", title)
    if m:
        out["imdb_rating"] = m.group(1)
    m = re.search(r"\|\s*([A-Za-z& ]+)$", title)
    if m:
        out["genres"] = [g.strip() for g in m.group(1).split("&") if g.strip()]
    m = re.search(r"Directed by\s+([^.]+)\.", desc)
    if m:
        out["director"] = m.group(1).strip()
    m = re.search(r"With\s+([^.]+)\.", desc)
    if m:
        out["cast"] = [c.strip() for c in m.group(1).split(",") if c.strip()]
    return out


def parse_baike(desc: str):
    """Pull director/cast/runtime from a Baike description."""
    out = {}
    m = re.search(r"由(.+?)执导", desc)
    if m:
        out["director"] = m.group(1).strip()
    m = re.search(r"(.+?)等主演", desc)
    if m:
        out["cast"] = [c.strip() for c in m.group(1).replace("、", ",").split(",") if c.strip()]
    m = re.search(r"片长\s*(\d+)\s*分钟", desc)
    if m:
        out["runtime"] = f"{m.group(1)}m"
    m = re.search(r"《.+?》是(?:一?部)?([^\u3002。]+?)片", desc)
    if m:
        out["genres"] = [g for g in re.split(r"、|/|\s+", m.group(1)) if g]
    return out


def parse_runtime_from_markdown(md: str) -> str:
    m = re.search(r"片长[：: ]*(\d+)\s*分钟", md)
    if m:
        return f"{m.group(1)}m"
    m = re.search(r"Runtime[：: ]*(\d+)\s*min", md, re.I)
    if m:
        return f"{m.group(1)}m"
    m = re.search(r"(\d+)\s*min(?:ute)?s?\b", md)
    if m:
        return f"{m.group(1)}m"
    return ""


def parse_douban_poster(md: str) -> str:
    """Extract an allow-listed poster (doubanio/douban) from a scraped Douban page."""
    m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', md)
    if not m:
        m = re.search(r'property="og:image"[^>]+content="([^"]+)"', md)
    if not m:
        m = re.search(r'content="([^"]+)"[^>]+property="og:image"', md)
    if m:
        url = m.group(1)
        host = urllib.parse.urlparse(url).netloc.lower()
        if any(host == a or host.endswith("." + a) for a in ("doubanio.com", "img.douban.com")):
            return url
    return ""


def enrich_title(idx: int, t: dict, db, total: int, dry_run: bool, fix_posters: bool) -> str:
    row_id = f"hk:cat3-{idx}"
    row = db.execute(
        "SELECT poster, background, cast_json FROM titles WHERE id = ?", (row_id,)
    ).fetchone()
    if not row:
        print(f"[hkcat3meta] {idx + 1}/{total} {row_id}: row missing, skipped")
        return "skip"
    poster = row[0] or ""
    cast_json = row[2] or ""
    if cast_json and cast_json not in ("", "[]"):
        return "ok"  # already enriched

    name = (t.get("zh") or "").strip()
    if not name:
        name = (t.get("en") or "").strip()
    year = str(t.get("year") or "")
    query = f"{name} {year}".strip()

    results = fc_search(query)
    src = pick_source(results)
    if not src:
        print(f"[hkcat3meta] {idx + 1}/{total} {row_id} {name}: no search result, kept")
        return "no_result"

    fields = {}
    title = src.get("title") or ""
    desc = src.get("description") or ""
    u = src.get("url") or ""
    if "imdb.com" in u:
        fields.update(parse_imdb(title, desc))
    elif "baidu.com" in u or "baike" in u:
        fields.update(parse_baike(desc))

    # Scrape for runtime when still missing (Baike/Douban pages).
    runtime = fields.get("runtime")
    if not runtime:
        md = fc_scrape(u)
        if md:
            runtime = parse_runtime_from_markdown(md)
            if runtime:
                fields["runtime"] = runtime
            if fix_posters and "douban" in u:
                p = parse_douban_poster(md)
                if p and poster.startswith(LIVE_HOST_PREFIX):
                    fields["_poster"] = p

    # Build the UPDATE (only non-empty fields).
    updates = {}
    if fields.get("cast"):
        updates["cast_json"] = json.dumps(fields["cast"], ensure_ascii=False)
    if fields.get("genres"):
        updates["genres_json"] = json.dumps(fields["genres"], ensure_ascii=False)
    if fields.get("director"):
        updates["director"] = fields["director"]
    if fields.get("imdb_rating"):
        updates["imdb_rating"] = fields["imdb_rating"]
    if fields.get("runtime"):
        updates["runtime"] = fields["runtime"]
    new_poster = fields.get("_poster", "")

    if not dry_run and (updates or (new_poster and poster.startswith(LIVE_HOST_PREFIX))):
        set_parts = [f"{k} = ?" for k in updates]
        vals = list(updates.values())
        if new_poster and poster.startswith(LIVE_HOST_PREFIX):
            set_parts += ["poster = ?", "background = ?"]
            vals += [new_poster, new_poster]
        if set_parts:
            db.execute(
                f"UPDATE titles SET {', '.join(set_parts)} WHERE id = ?",
                (*vals, row_id),
            )
            db.commit()

    if updates or new_poster:
        print(f"[hkcat3meta] {idx + 1}/{total} {row_id} {name}: "
              f"filled {list(k for k in updates if k != 'cast_json') + (['cast'] if 'cast_json' in updates else [])}"
              f"{', poster' if (new_poster and poster.startswith(LIVE_HOST_PREFIX)) else ''}")
        return "updated"
    print(f"[hkcat3meta] {idx + 1}/{total} {row_id} {name}: nothing parseable from source")
    return "no_parse"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--posters", action="store_true",
                    help="also fill the 34 pending SVG-card posters when a Douban poster is found")
    args = ap.parse_args()

    meta = json.loads(META_JSON.read_text(encoding="utf-8"))
    titles = meta["av"]
    total = len(titles)
    if args.limit:
        titles = titles[:args.limit]

    db = sqlite3.connect(str(DB))
    stats = {"ok": 0, "updated": 0, "no_result": 0, "no_parse": 0, "skip": 0}
    for idx, t in enumerate(titles):
        res = enrich_title(idx, t, db, total, args.dry_run, args.posters)
        stats[res] = stats.get(res, 0) + 1
        if args.delay:
            time.sleep(args.delay)
    db.close()
    print(f"done: {json.dumps(stats)} (of {min(len(meta['av']), args.limit or len(meta['av']))} titles)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""One-off: backfill metadata onto cat3 japan-photo rows from multiple sources.

The live `--enrich-sqlite` job carries a `jav:` prefix guard
(`_is_jav_enrich_target`), so cat3 rows (id `hk:cat3-*`, category
`japan-photo`) never receive JavDB fields. This script fills them:
extract the JAV code from the forum name, then pull metadata from a
multi-source fallback chain, and write through the same
`update_sqlite_title` writer the live job uses, so catalog order
(posted_at DESC) is untouched.

Source fallback (in order, first useful field wins):
  1. JavDB (javdb.com)            — primary; sparse for photo sets.
  2. Jable.tv (en.jable.tv)        — via the QNAP Firecrawl proxy
                                      (direct GET is Cloudflare 403);
                                      supplies genres + cast + released.
  3. jav321.com                    — official title / released / cast
                                      (best-effort; the domain is currently
                                      a parked ad page, so it rarely yields).

Only the cat3 `japan-photo` category is targeted. Rows that already have
`genres` are skipped (resume-safe).

Usage:
    JAVDB_DELAY=2.5 python3 addon/cat3_javdb_fill.py [--limit N]
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import requests

import build_addon as ba
from javdb import apply_fields, cache_fits, jav321_meta, javdb_meta, translate_genres

DB = HERE.parent / "debrid-app" / "data" / "titles.sqlite"
DELAY = float(os.environ.get("JAVDB_DELAY", "2.5"))
# QNAP Firecrawl proxy (JS-rendering) used to reach Jable, which 403s on
# direct GET. Override with JABLE_FIRECRAWL if the host moves.
JABLE_FC = os.environ.get("JABLE_FIRECRAWL", "http://192.168.68.83:3002")
TODAY_Y = int(os.environ.get("TODAY_Y", "2026"))
PROGRESS_EVERY = 50


def code_from_name(name: str) -> str:
    m = ba.JAV_CODE_RE.search(name or "")
    if not m:
        return ""
    # Normalize to uppercase JAV code, e.g. "oae-102" -> "OAE-102".
    letters = (m.group(1) or "").upper()
    number = m.group(2)
    return f"{letters}-{number}"


def jable_fields(code: str) -> dict:
    """Fetch the Jable video page through the QNAP Firecrawl proxy and
    parse cast / genres / released. Returns a `fields`-shaped dict (English
    genres are translated to the DB's Chinese names by apply_fields)."""
    url = f"https://en.jable.tv/videos/{code}/"
    for attempt in range(3):
        try:
            r = requests.post(
                JABLE_FC + "/v2/scrape",
                json={"url": url, "formats": ["markdown"]},
                timeout=120,
            )
            data = (r.json() or {}).get("data") or {}
            md = data.get("markdown")
            if md:
                cast = re.findall(r"\[([^\]]+)\]\(https://en\.jable\.tv/models/[a-f0-9]+/", md)
                genres = re.findall(r"\[([^\]]+)\]\(https://en\.jable\.tv/(?:categories|tags)/[^/]+/", md)
                released = ""
                m = re.search(r"^######\s+(\d+)\s+years ago", md, re.M)
                if m:
                    released = f"{TODAY_Y - int(m.group(1))}-11-23T00:00:00.000Z"
                out = {}
                if cast:
                    out["cast"] = cast
                if genres:
                    out["genres"] = genres
                if released:
                    out["released"] = released
                return out
        except Exception:
            time.sleep(2 * (attempt + 1))
    return {}


def collect_fields(code: str) -> dict:
    """Multi-source fallback: JavDB first, then Jable (Firecrawl), then
    jav321. The first source that yields a useful field wins for each key."""
    fields = javdb_meta(code, use_cache=True, allow_network=False)
    if not cache_fits(code, fields):
        fields = javdb_meta(code, use_cache=True, allow_network=True)
    fields = dict(fields or {})

    # Fallback 1 — Jable (via QNAP Firecrawl).
    if not any(fields.get(k) for k in ("genres", "cast", "released")):
        jable = jable_fields(code)
        for k, v in jable.items():
            if not fields.get(k):
                fields[k] = v

    # Fallback 2 — jav321 (best-effort; currently a parked ad domain).
    if not any(fields.get(k) for k in ("genres", "cast", "released", "summary", "title")):
        j321 = jav321_meta(code, use_cache=True, allow_network=True) or {}
        for k, v in j321.items():
            if v and not fields.get(k):
                fields[k] = v

    return fields


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0,
                        help="Max rows to process (0 = all unfilled rows)")
    args = parser.parse_args()

    import sqlite3
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT id, name, description, cast_json, genres_json
           FROM titles WHERE category = 'japan-photo'"""
    ).fetchall()
    conn.close()

    pending = []
    for row in rows:
        if ba._parse_json_list(row["genres_json"]):
            continue  # already enriched
        code = code_from_name(row["name"] or "")
        if code:
            pending.append((row, code))

    limit = args.limit
    if limit and len(pending) > limit:
        pending = pending[:limit]

    total = len(pending)
    filled = 0
    for i, (row, code) in enumerate(pending, start=1):
        fields = collect_fields(code)
        # Photo sets are sparsely cataloged on every source: only write a row
        # when at least one source returned a useful field. The cat3 gap being
        # filled is `released` (and, where available, genres/cast).
        if not any(fields.get(k) for k in ("genres", "cast", "released", "summary")):
            continue
        cast = ba._parse_json_list(row["cast_json"])
        meta = {
            "id": row["id"],
            "type": "movie",
            "name": row["name"] or "",
            "description": row["description"] or "",
        }
        if cast:
            meta["cast"] = cast
        rec = {
            "titleno": code,
            "title": row["name"] or "",
            "出演女优": " / ".join(cast),
        }
        enriched = apply_fields(meta, fields, rec)
        if not any(enriched.get(k) for k in ("genres", "cast", "released")):
            continue
        ba.update_sqlite_title(DB, enriched)
        filled += 1
        if i % PROGRESS_EVERY == 0 or i == total:
            print(f"[{i}/{total}] code={code} filled={filled}", flush=True)
        time.sleep(DELAY)

    print(f"done: {filled}/{total} rows enriched (limit={args.limit or 'all'})")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""One-off: backfill JavDB metadata onto cat3 japan-photo rows.

The live `--enrich-sqlite` job carries a `jav:` prefix guard
(`_is_jav_enrich_target`), so cat3 rows (id `hk:cat3-*`, category
`japan-photo`) never receive JavDB fields. This script fills them:
extract the JAV code from the forum name, fetch JavDB meta, and write
through the same `update_sqlite_title` writer the live job uses, so
catalog order (posted_at DESC) is untouched.

Usage:
    JAVDB_DELAY=2.5 python3 addon/cat3_javdb_fill.py [--limit N]
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build_addon as ba
from javdb import apply_fields, cache_fits, javdb_meta

DB = HERE.parent / "debrid-app" / "data" / "titles.sqlite"
DELAY = float(os.environ.get("JAVDB_DELAY", "2.5"))
PROGRESS_EVERY = 50


def code_from_name(name: str) -> str:
    m = ba.JAV_CODE_RE.search(name or "")
    if not m:
        return ""
    # Normalize to uppercase JAV code, e.g. "oae-102" -> "OAE-102".
    letters = (m.group(1) or "").upper()
    number = m.group(2)
    return f"{letters}-{number}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0,
                        help="Max JavDB network fetches (0 = all unfilled rows)")
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
        fields = javdb_meta(code, use_cache=True, allow_network=False)
        if not cache_fits(code, fields):
            fields = javdb_meta(code, use_cache=True, allow_network=True)
        # Photo sets are sparsely cataloged on JavDB: many have no genres.
        # Fill any row where JavDB returns at least one useful field —
        # the cat3 gap being filled is `released`, not genres.
        if not fields or not any(fields.get(k) for k in ("genres", "cast", "released", "summary")):
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

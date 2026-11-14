#!/usr/bin/env python3
"""Fill genres (and cast/director/runtime) for sehuatang-sourced HK rows.

The forum-107 scrape already stores poster + magnet; this job adds the
missing genres so the hongkong meta responses are complete. It reads the
live SQLite directly (not the Wikipedia JSON), so it works for the new
`hk:cat3-<threadid>` rows. Genres come from self-hosted Firecrawl
(/v2/search + /v2/scrape) reusing the parsers in hk_cat3_meta_enrich.

Usage:
    python3 addon/hk_cat3_genres.py [--limit N] [--delay SEC] [--dry-run]
"""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "debrid-app" / "data" / "titles.sqlite"

# Reuse the Firecrawl + parse helpers already written for the meta-enrich job.
from hk_cat3_meta_enrich import (
    fc_search,
    fc_scrape,
    pick_source,
    parse_imdb,
    parse_baike,
    parse_flat_desc,
    parse_genres_from_markdown,
    parse_runtime_from_markdown,
)


def filled(value: str) -> bool:
    return bool(value) and value not in ("", "[]", "null")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = sqlite3.connect(str(DB))
    rows = db.execute(
        "SELECT id, name, genres_json, cast_json, director, runtime "
        "FROM titles WHERE id LIKE 'hk:cat3-%' ORDER BY posted_at DESC"
    ).fetchall()
    if args.limit:
        rows = rows[:args.limit]

    filled_n = skipped = no_result = no_parse = 0
    for row_id, name, genres_json, cast_json, director, runtime in rows:
        if filled(genres_json):
            skipped += 1
            continue

        query = (name or "").strip()
        results = fc_search(query)
        src = pick_source(results)
        if not src:
            no_result += 1
            print(f"[hkcat3genres] {row_id}: no search result for '{name}'")
            if args.delay:
                time.sleep(args.delay)
            continue

        fields = {}
        u = src.get("url") or ""
        title = src.get("title") or ""
        desc = src.get("description") or ""
        if "imdb.com" in u:
            fields.update(parse_imdb(title, desc))
        if "baidu.com" in u or "baike" in u:
            fields.update(parse_baike(desc))
        # Flat key-value descriptions (Douban/Baike) carry 类型/导演/主演/片长.
        fields.update(parse_flat_desc(desc))

        # Scrape the page for genres/runtime when the search desc had none.
        if not fields.get("genres"):
            md = fc_scrape(u)
            if md:
                genres = parse_genres_from_markdown(md)
                if genres:
                    fields["genres"] = genres
                if not fields.get("runtime"):
                    rt = parse_runtime_from_markdown(md)
                    if rt:
                        fields["runtime"] = rt

        updates = {}
        if fields.get("genres"):
            updates["genres_json"] = json.dumps(fields["genres"], ensure_ascii=False)
        if fields.get("cast"):
            updates["cast_json"] = json.dumps(fields["cast"], ensure_ascii=False)
        if fields.get("director"):
            updates["director"] = fields["director"]
        if fields.get("runtime"):
            updates["runtime"] = fields["runtime"]

        if updates:
            if not args.dry_run:
                set_parts = [f"{k} = ?" for k in updates]
                db.execute(
                    f"UPDATE titles SET {', '.join(set_parts)} WHERE id = ?",
                    (*updates.values(), row_id),
                )
                db.commit()
            filled_n += 1
            print(f"[hkcat3genres] {row_id}: filled {list(updates.keys())}")
        else:
            no_parse += 1
            print(f"[hkcat3genres] {row_id}: nothing parseable for '{name}'")
        if args.delay:
            time.sleep(args.delay)

    db.close()
    print(
        f"done: filled={filled_n} skipped={skipped} "
        f"no_result={no_result} no_parse={no_parse} (of {len(rows)} rows)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

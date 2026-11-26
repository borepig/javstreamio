#!/usr/bin/env python3
"""One-off: replace the "NOW PRINTING" DMM posters (and AI-generated
posters) on JAV rows with the JavDB jdbstatic cover, and backfill the
JavDB metadata (cast / genres / released / runtime / title / summary).

Target: `jav:` rows in jav-censored / jav-uncensored / western whose code
prefix is one of JUKF, YUME, YSN, YAG, WFS, ABF, NASH (the prefixes the
user reported showing "NOW PRINTING" or AI-generated art).

For each target row:
  1. code = id suffix (e.g. `jav:ABF-385` -> `ABF-385`).
  2. metadata comes from the JavDB cache (offline, no network). Codes
     missing from the cache fall back to Jable (via the QNAP Firecrawl
     proxy) — reuses `jable_fields` from cat3_javdb_fill.py.
  3. The poster/background becomes the JavDB jdbstatic cover
     `https://c0.jdbstatic.com/covers/<2-letter-prefix>/<code>.jpg`
     (portrait 3:4, no 9:16 crop needed). `dmm_missing` is set to 1 so
     `applyPublicArt` skips the DMM ps/pl fallback and serves the stored
     JavDB cover (DB-stored public art wins — see the applyPublicArt
     overwrite pitfall).
  4. Cast is merged (forum actresses are preserved, JavDB cast appended —
     never drop an existing credit), genres are translated to the DB's
     Chinese names, `released` is normalised to RFC3339, runtime / rating
     / official title + summary are backfilled into the meta.

`posted_at` and `category` are left untouched so the catalog stays
newest-first (the live `/meta` + `/catalog` order is preserved).

Usage:
    python3 addon/jav_nowprinting_fix.py [--limit N] [--dry-run]
"""
import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build_addon as ba
from cat3_javdb_fill import jable_fields
from javdb import apply_fields, javdb_meta, translate_genres

DB = HERE.parent / "debrid-app" / "data" / "titles.sqlite"
PREFIXES = ("JUKF", "YUME", "YSN", "YAG", "WFS", "ABF", "NASH")
CATEGORIES = ("jav-censored", "jav-uncensored", "western")


def code_from_id(row_id: str) -> str:
    # `jav:ABF-385` -> `ABF-385`
    raw = row_id.split(":", 1)[-1]
    return raw.upper()


def jdbstatic_cover(code: str) -> str:
    low = code.lower()
    return f"https://c0.jdbstatic.com/covers/{low[:2]}/{low}.jpg"


def collect_fields(code: str) -> dict:
    """JavDB cache first; Jable (Firecrawl) on a cache miss."""
    fields = javdb_meta(code, use_cache=True, allow_network=False) or {}
    if not any(fields.get(k) for k in ("genres", "cast", "released", "title", "summary")):
        jable = jable_fields(code)
        for k, v in jable.items():
            if v and not fields.get(k):
                fields[k] = v
    return fields


def _parse_json_list(raw) -> list:
    return ba._parse_json_list(raw)


def update_row(conn, row, fields, code) -> None:
    cover = jdbstatic_cover(code)
    cast = _parse_json_list(row["cast_json"])
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

    released = ba.rfc3339_released(enriched.get("released")) or ""
    release_info = (released[:4] if released else "")
    director = ba._director_text(enriched.get("director"))
    conn.execute(
        """
        UPDATE titles SET
          name = COALESCE(NULLIF(?, ''), name),
          description = COALESCE(NULLIF(?, ''), description),
          release_info = COALESCE(NULLIF(?, ''), release_info),
          released = COALESCE(NULLIF(?, ''), released),
          runtime = COALESCE(NULLIF(?, ''), runtime),
          cast_json = ?,
          genres_json = ?,
          director = COALESCE(NULLIF(?, ''), director),
          imdb_rating = COALESCE(NULLIF(?, ''), imdb_rating),
          poster = ?,
          background = ?,
          dmm_missing = 1,
          search_text = ?
        WHERE id = ?
        """,
        (
            enriched.get("name") or "",
            enriched.get("description") or "",
            release_info,
            released,
            enriched.get("runtime") or "",
            json.dumps(enriched.get("cast") or [], ensure_ascii=False),
            json.dumps(enriched.get("genres") or [], ensure_ascii=False),
            director,
            enriched.get("imdbRating") or "",
            cover,
            cover,
            ba.title_search_text(enriched),
            row["id"],
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0,
                        help="Max rows to process (0 = all target rows)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing")
    args = parser.parse_args()

    import sqlite3
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    like = " OR ".join(f"id LIKE 'jav:{p}-%'" for p in PREFIXES)
    cats = ",".join("?" * len(CATEGORIES))
    rows = conn.execute(
        f"""
        SELECT id, name, description, cast_json, genres_json
        FROM titles
        WHERE ({like}) AND category IN ({cats})
        """,
        list(CATEGORIES),
    ).fetchall()

    pending = []
    for row in rows:
        code = code_from_id(row["id"])
        if code:
            pending.append((row, code))

    limit = args.limit
    if limit and len(pending) > limit:
        pending = pending[:limit]

    filled = 0
    for i, (row, code) in enumerate(pending, start=1):
        fields = collect_fields(code)
        if not any(fields.get(k) for k in ("genres", "cast", "released", "title", "summary")):
            # No source had useful metadata for this code — still swap the
            # poster to the JavDB cover (the "NOW PRINTING" fix), skip the
            # metadata backfill.
            if not args.dry_run:
                update_row(conn, row, {}, code)
                filled += 1
            continue
        if not args.dry_run:
            update_row(conn, row, fields, code)
            filled += 1
        if i % 50 == 0 or i == len(pending):
            print(f"[{i}/{len(pending)}] last={code} filled={filled}", flush=True)

    if not args.dry_run:
        conn.commit()
    conn.close()
    print(f"done: {filled}/{len(pending)} rows updated (limit={args.limit or 'all'}"
          + (", dry-run" if args.dry_run else "") + ")")


if __name__ == "__main__":
    main()

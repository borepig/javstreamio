#!/usr/bin/env python3
"""Split the single 'hongkong' CAT III library into per-country catalogs.

The sehuatang forum-107 jsonl (data/forum-107-all.jsonl) carries each
thread's subcategory `tag` (韩国三级 / 法国写真 / ...). This job maps
thread id -> catalog per the user-approved mapping and re-tags
titles.category in the live SQLite. Resume-safe and idempotent: rows
already carrying their target catalog are skipped.

Mapping (2026-11-15, user-approved):
  korea:         韩国三级
  hongkong:       香港三级 + 香港四级
  western:         美国三级 + 美国四级
  japan:          日本三级
  japan-photo:    日本写真
  france:         法国三级 + 法国四级
  cat3-others:    every other subcategory (国产/写真/意大利/台湾/...)
"""
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "debrid-app" / "data" / "titles.sqlite"
JSONL = ROOT / "data" / "forum-107-all.jsonl"
THREAD_RE = re.compile(r"thread-(\d+)")

TAG_TO_CATALOG = {
    "韩国三级": "korea",
    "香港三级": "hongkong",
    "香港四级": "hongkong",
    "美国三级": "western",
    "美国四级": "western",
    "日本三级": "japan",
    "日本写真": "japan-photo",
    "法国三级": "france",
    "法国四级": "france",
}
DEFAULT_CATALOG = "cat3-others"
NEW_CATEGORIES = ("korea", "hongkong", "western", "japan", "france", "japan-photo", "cat3-others")


def tid_catalog_map() -> dict:
    """thread id -> target catalog, from the forum-107 jsonl tag field."""
    mapping = {}
    with JSONL.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            m = THREAD_RE.search(rec.get("url") or "")
            if not m:
                continue
            tag = rec.get("tag") or ""
            mapping[m.group(1)] = TAG_TO_CATALOG.get(tag, DEFAULT_CATALOG)
    return mapping


def main():
    mapping = tid_catalog_map()
    conn = sqlite3.connect(DB, timeout=30)
    # Materialize the rows first: running UPDATE on the same connection
    # while iterating a lazy cursor corrupts the iteration (rows get
    # re-visited, counts go wrong).
    rows = list(conn.execute("SELECT id, category FROM titles WHERE id LIKE 'hk:cat3-%'"))
    conn.close()

    updated = 0
    already = 0
    unknown = 0
    conn = sqlite3.connect(DB, timeout=30)
    for row_id, current in rows:
        tid = row_id.rsplit("-", 1)[-1]
        target = mapping.get(tid)
        if target is None:
            # Row predates the forum-107 re-sourcing (older HK library rows);
            # keep its current category.
            unknown += 1
            continue
        if current == target:
            already += 1
            continue
        conn.execute("UPDATE titles SET category = ? WHERE id = ?", (target, row_id))
        conn.commit()  # per-row commit: resume-safe
        updated += 1

    conn = sqlite3.connect(DB, timeout=30)
    # Scope the report to the hk split rows. 'western' also holds pre-existing
    # JAV western titles (served under the same 'western' catalog), so count only
    # the hk:cat3 rows here.
    per_cat = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT category, count(*) FROM titles "
            f"WHERE category IN {NEW_CATEGORIES} AND id LIKE 'hk:cat3-%' GROUP BY category"
        )
    }
    conn.close()
    print(f"tid->catalog mappings: {len(mapping)}")
    print(f"updated: {updated}")
    print(f"already-correct: {already}")
    print(f"not in jsonl (kept as-is): {unknown}")
    print("per-catalog counts after split:")
    for cat in NEW_CATEGORIES:
        print(f"  {cat}: {per_cat.get(cat, 0)}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""One-time data fix: re-translate stored English JAV genres to Chinese.

The live titles.sqlite stores JavDB genre values; some were fetched with a
locale=en session, so a subset were stored in English. This script applies
``javdb.translate_genres`` to every JAV row's ``genres_json`` and rewrites
the column only when the translation actually changes a value.

Usage:
    python3 addon/retell_zh.py [path/to/titles.sqlite]

With no argument it targets the live DB at debrid-app/data/titles.sqlite.
Run it locally and (via scp) on the EC2 host to fix the stored rows.
"""
import json
import os
import sys
from pathlib import Path

from javdb import translate_genres

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT.parent / "debrid-app" / "data" / "titles.sqlite"
JAV_CATEGORIES = ("jav-censored", "jav-uncensored")


def main() -> None:
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    # Pull JAV rows with non-empty genres.
    rows = cur.execute(
        "SELECT id, genres_json FROM titles "
        "WHERE category IN ('jav-censored','jav-uncensored') "
        "AND genres_json IS NOT NULL AND genres_json NOT IN ('','[]')"
    ).fetchall()

    changed = 0
    for tid, raw in rows:
        try:
            arr = json.loads(raw)
        except Exception:
            continue
        if not isinstance(arr, list):
            continue
        translated = translate_genres(arr)
        if translated != arr:
            cur.execute(
                "UPDATE titles SET genres_json = ? WHERE id = ?",
                (json.dumps(translated, ensure_ascii=False), tid),
            )
            changed += 1
    conn.commit()
    conn.close()
    print(f"retell-zh: {len(rows)} JAV rows scanned, {changed} rows re-translated.")


if __name__ == "__main__":
    main()

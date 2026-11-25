#!/usr/bin/env python3
"""One-time data fix: split stored space-joined JAV cast strings into names.

Some JavDB rows stored the whole multi-actress cast as a single space-joined
string (e.g. "綾瀬こころ 依本しおり 宮瀬夏純 あかね麗") instead of one name
per actress — the detail page showed a "cut off" blob. This script re-splits
every JAV row's ``cast_json`` on whitespace so each actress is a distinct
credit. A "・" inside a name is kept (not a split point).

Usage:
    python3 addon/retell_cast.py [path/to/titles.sqlite]

With no argument it targets debrid-app/data/titles.sqlite. Run it locally and
(scp'd to /opt/javstreamio) on the EC2 host.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT.parent / "debrid-app" / "data" / "titles.sqlite"


def _split_names(raw: str) -> list[str]:
    return [n for n in re.split(r"\s+", (raw or "").strip()) if n]


def main() -> None:
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT id, cast_json FROM titles "
        "WHERE category IN ('jav-censored','jav-uncensored') "
        "AND cast_json IS NOT NULL AND cast_json NOT IN ('','[]')"
    ).fetchall()

    changed = 0
    for tid, raw in rows:
        try:
            arr = json.loads(raw)
        except Exception:
            continue
        if not isinstance(arr, list):
            continue
        new_arr = []
        modified = False
        for name in arr:
            s = str(name)
            if " " in s:
                parts = _split_names(s)
                if len(parts) > 1:
                    new_arr.extend(parts)
                    modified = True
                else:
                    new_arr.append(s)
            else:
                new_arr.append(s)
        # De-dup case-insensitively, preserving first occurrence order.
        seen = set()
        dedup = []
        for n in new_arr:
            key = n.lower()
            if key in seen:
                continue
            seen.add(key)
            dedup.append(n)
        if modified or len(dedup) != len(arr):
            cur.execute(
                "UPDATE titles SET cast_json = ? WHERE id = ?",
                (json.dumps(dedup, ensure_ascii=False), tid),
            )
            changed += 1
    conn.commit()
    conn.close()
    print(f"retell-cast: {len(rows)} JAV rows scanned, {changed} rows re-split.")


if __name__ == "__main__":
    main()

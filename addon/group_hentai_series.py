#!/usr/bin/env python3
"""Group hnt:* (Sukebei/Nyaa) rows into Stremio series in the live SQLite.

Hentai titles behave like TV shows: a collection of episodes. This script
adds the `titles.series_key` / `titles.episode_num` columns when missing,
then parses every hnt row's name (reusing enrich_hentai.parse_sukebei_name)
and writes the grouping:

- parseable series name  -> series_key = slugified series name, episode = E# (1 for OVA)
- unparseable/garbage name -> its own one-episode series: "solo-<nyaa-id>"

Idempotent: re-running recomputes the grouping for every hnt row (e.g. after
enrich_hentai.py recovers truncated names).

Usage (from repo root):
    python3 addon/group_hentai_series.py             # add columns + fill grouping
    python3 addon/group_hentai_series.py --dry-run   # show the plan, write nothing
"""

import argparse
import os
import re
import sqlite3

from build_addon import TITLES_SQLITE
from enrich_hentai import parse_sukebei_name, slugify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "debrid-app", "data", "titles.sqlite") if not os.path.exists(TITLES_SQLITE) else TITLES_SQLITE


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="Show the grouping plan without writing.")
    args = ap.parse_args()

    db = sqlite3.connect(DB)
    cur = db.cursor()
    cols = [r[1] for r in cur.execute("PRAGMA table_info(titles)").fetchall()]
    if "series_key" not in cols:
        cur.execute("ALTER TABLE titles ADD COLUMN series_key TEXT DEFAULT ''")
        print("added titles.series_key column")
    if "episode_num" not in cols:
        cur.execute("ALTER TABLE titles ADD COLUMN episode_num INTEGER DEFAULT 0")
        print("added titles.episode_num column")
    db.commit()

    rows = cur.execute(
        "SELECT id, name FROM titles WHERE id LIKE 'hnt:%' ORDER BY id"
    ).fetchall()

    grouped = {}
    for rid, name in rows:
        series, episode = parse_sukebei_name(name)
        series = str(series or "").strip()
        nyaa_m = re.match(r"hnt:nyaa-(\d+)", rid)
        if series and not re.fullmatch(r"\d+", series):
            key = slugify(series)
            ep = episode if episode is not None else 1
        else:
            key = f"solo-{nyaa_m.group(1) if nyaa_m else rid}"
            ep = 1
        if not key:
            continue
        grouped.setdefault(key, []).append((rid, ep))
        if not args.dry_run:
            cur.execute(
                "UPDATE titles SET series_key = ?, episode_num = ? WHERE id = ?",
                (key, ep, rid),
            )
    if not args.dry_run:
        db.commit()

    db.close()
    eps = sum(len(v) for v in grouped.values())
    print(f"{'plan' if args.dry_run else 'written'}: {len(grouped)} series, {eps} episodes")
    for key, items in sorted(grouped.items()):
        if len(items) > 1 or key.startswith("solo-"):
            continue
    top = sorted(grouped.items(), key=lambda kv: -len(kv[1]))[:10]
    for key, items in top:
        print(f"  {key}: {len(items)} episodes")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Curate the hnt:* (hentai censored/uncensored) rows in the live SQLite.

The Nyaa/Sukebei "hentai" / "uncen" search also surfaces live-action JAV
(Japanese adult video) torrents that are NOT hentai (anime). This script
removes those non-hentai rows from the two hnt categories, then re-runs the
series grouping for the remaining rows.

Non-hentai rows are identified by live-action JAV signals in the name:
JAV brand/label codes (FC2PPV, CARIBBEANCOM, HEYZO, MIAB, ...) and English
JAV descriptors (JAV / BBC / creampie). Idempotent + resume-safe: re-running
only removes rows that still match.

Usage (from repo root):
    python3 addon/curate_hnt.py --dry-run   # show what would be removed
    python3 addon/curate_hnt.py             # remove + regroup
"""

import argparse
import os
import re
import sqlite3

from build_addon import TITLES_SQLITE
from enrich_hentai import parse_sukebei_name, slugify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = TITLES_SQLITE if TITLES_SQLITE and os.path.exists(str(TITLES_SQLITE)) else os.path.join(ROOT, "debrid-app", "data", "titles.sqlite")

HNT_CATEGORIES = ("hentai-censored", "hentai-uncensored")

# Live-action JAV signals (case-insensitive, matched against the lowercased
# name). Anime-hentai releases ([HentaiHub]/[Doomdos]/[geckyzz]/[Patreon]
# etc.) never contain these, so matching them is safe.
JAV_BRAND_CODES = (
    "FC2PPV", "FC2 PPV", "FC2-PPV", "CARIBBEANCOM", "HEYZO", "PACOPACOMAMA",
    "MIAB", "MIAC", "MIAD", "MIAA", "MOED", "NACR", "NNOD", "STSK",
    "YMRK", "DLDSS", "SGA", "STARS", "START", "XVSR", "SNOS", "UMD",
    "BAGR", "HBAD", "MOND", "MXGS", "MOGI", "1PONDO", "10MUSUME",
)
# N0443-N0467 are JAV (N-code labels).
JAV_N_CODES = ("N0443", "N0444", "N0445", "N0459", "N0460", "N0461",
                "N0462", "N0463", "N0464", "N0465", "N0466", "N0467")
JAV_ENG_DESCRIPTORS = ("JAV", "BBC", "CREAMPIE")

# A JAV code counts as a hit when it is NOT directly followed by an ASCII
# letter: "MONDAIJI" (a Japanese title word) therefore does not match
# "MOND", while "MOND-308", "UncensoredCaribbeancom" and "UncensoredFC2PPV"
# (glued "Uncensored"+code) do.
def _code_pattern(code: str) -> str:
    return re.escape(code) + r"(?![A-Za-z])"

_NON_HENTAI_CODE_RE = re.compile(
    "|".join(_code_pattern(c) for c in list(JAV_BRAND_CODES) + list(JAV_N_CODES)),
    re.IGNORECASE,
)


def is_non_hentai(name: str) -> bool:
    n = str(name or "")
    if _NON_HENTAI_CODE_RE.search(n):
        return True
    return any(s in n.upper() for s in JAV_ENG_DESCRIPTORS)


def regroup(db: sqlite3.Connection, dry_run: bool) -> None:
    """Recompute series_key/episode_num for the remaining hnt rows."""
    cols = [r[1] for r in db.execute("PRAGMA table_info(titles)").fetchall()]
    if "series_key" not in cols:
        db.execute("ALTER TABLE titles ADD COLUMN series_key TEXT DEFAULT ''")
    if "episode_num" not in cols:
        db.execute("ALTER TABLE titles ADD COLUMN episode_num INTEGER DEFAULT 0")
    rows = db.execute("SELECT id, name FROM titles WHERE id LIKE 'hnt:%' ORDER BY id").fetchall()
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
        if not dry_run:
            db.execute("UPDATE titles SET series_key = ?, episode_num = ? WHERE id = ?",
                       (key, ep, rid))


def main() -> None:
    ap = argparse.ArgumentParser(description="Remove non-hentai (live-action JAV) rows from the two hnt categories and regroup the rest.")
    ap.add_argument("--dry-run", action="store_true", help="Show what would be removed, write nothing.")
    args = ap.parse_args()

    db = sqlite3.connect(str(DB))
    db.row_factory = sqlite3.Row

    # Identify + remove non-hentai rows.
    flagged = []
    for cat in HNT_CATEGORIES:
        for row in db.execute("SELECT id, name FROM titles WHERE category = ?", (cat,)):
            if is_non_hentai(row["name"]):
                flagged.append((cat, row["id"], row["name"]))

    print(f"{'plan' if args.dry_run else 'removed'}: {len(flagged)} non-hentai rows")
    by_cat = {}
    for cat, rid, name in flagged:
        by_cat.setdefault(cat, []).append(rid)
    for cat in HNT_CATEGORIES:
        print(f"  {cat}: {len(by_cat.get(cat, []))} rows")
    for cat, rid, name in flagged:
        if not args.dry_run:
            db.execute("DELETE FROM titles WHERE id = ?", (rid,))

    # Regroup the remaining hnt rows.
    regroup(db, args.dry_run)
    if not args.dry_run:
        db.commit()

    if not args.dry_run:
        kept = db.execute("SELECT COUNT(*) FROM titles WHERE category IN ('hentai-censored','hentai-uncensored')").fetchone()[0]
        series = db.execute("SELECT COUNT(DISTINCT series_key) FROM titles WHERE category IN ('hentai-censored','hentai-uncensored') AND series_key != ''").fetchone()[0]
        print(f"kept {kept} hnt rows in {series} series")
    db.close()


if __name__ == "__main__":
    main()

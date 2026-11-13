#!/usr/bin/env python3
"""HK Cat III posters: douban subject_suggest -> live SQLite poster/background.

For each of the 172 HK Cat III titles (addon/hk_cat3_av.json), query
douban's subject_suggest endpoint (movie.douban.com/j/subject_suggest).
The best name/year match carries the poster URL (`img*.doubanio.com`).
Titles not found on douban get a generated SVG title card under
`debrid-app/public/posters/hkcat3/` (served from the live host, whose
hostname is already in the isPublicArtUrl allowlist in
debrid-app/lib/addon-data.js).

Resume-safe: rows that already have a poster are skipped.

Usage:
    python3 addon/hk_cat3_posters.py [--limit N] [--delay SEC] [--dry-run]
"""

import argparse
import difflib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
META_JSON = ROOT / "addon" / "hk_cat3_av.json"
DB = ROOT / "debrid-app" / "data" / "titles.sqlite"
POSTER_DIR = ROOT / "debrid-app" / "public" / "posters" / "hkcat3"
LIVE_ORIGIN = os.environ.get("LIVE_ORIGIN", "https://javstremio.ddns.net")
DOUBAN_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

# Rare/traditional variants that douban normalizes (e.g. 巿 -> 市).
CHAR_NORM = str.maketrans({"巿": "市"})


def norm_title(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.translate(CHAR_NORM)
    return re.sub(r"\s+", " ", s)


def douban_suggest(title: str) -> list:
    """Query douban subject_suggest; return the raw result list."""
    q = urllib.parse.quote(title)
    url = f"https://movie.douban.com/j/subject_suggest?q={q}"
    req = urllib.request.Request(url, headers={"User-Agent": DOUBAN_UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def best_match(title: str, results: list, year: str):
    """Pick the best douban hit: name similarity + year bonus."""
    best, best_score = None, -1.0
    for r in results:
        cand_title = (r.get("title") or "")
        score = difflib.SequenceMatcher(None, norm_title(title), norm_title(cand_title)).ratio()
        if r.get("year") and str(r["year"]) == str(year):
            score += 0.15
        if score > best_score:
            best, best_score = r, score
    if best and best_score >= 0.6 and best.get("img"):
        return best, best_score
    return None, best_score


def make_svg_card(slug: str, title: str, year: str) -> str:
    """Generate a 512x768 SVG title card, mirroring the hnt card format."""
    name = f"{title} {year}".strip()
    # Escape XML special chars in the CJK title (quotes unlikely, but be safe).
    name_xml = name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="768" viewBox="0 0 512 768">\n'
        "  <defs>\n"
        '    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">\n'
        '      <stop offset="0%" stop-color="hsl(345 55% 18%)"/>\n'
        '      <stop offset="100%" stop-color="hsl(25 65% 32%)"/>\n'
        "    </linearGradient>\n"
        '    <linearGradient id="frame" x1="0" y1="0" x2="0" y2="1">\n'
        '      <stop offset="0%" stop-color="#ffffff" stop-opacity="0.25"/>\n'
        '      <stop offset="100%" stop-color="#ffffff" stop-opacity="0"/>\n'
        "    </linearGradient>\n"
        "  </defs>\n"
        '  <rect width="512" height="768" fill="url(#bg)"/>\n'
        '  <rect x="12" y="12" width="488" height="744" rx="14" fill="none" stroke="url(#frame)" stroke-width="2"/>\n'
        f'  <text x="256" y="367" text-anchor="middle" font-family="'
        "Arial, 'Noto Sans CJK SC', 'PingFang SC', 'Microsoft YaHei', sans-serif"
        f'" font-size="30" font-weight="700" fill="#ffffff">{name_xml}</text>\n'
        '  <text x="256" y="736" text-anchor="middle" font-family="Arial, Helvetica, sans-serif" font-size="16" fill="#ffffff" fill-opacity="0.55">javstreamio</text>\n'
        "</svg>\n"
    )
    POSTER_DIR.mkdir(parents=True, exist_ok=True)
    path = POSTER_DIR / f"{slug.replace(':', '-')}.svg"
    path.write_text(svg, encoding="utf-8")
    return f"{LIVE_ORIGIN}/posters/hkcat3/{slug.replace(':', '-')}.svg"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    meta = json.loads(META_JSON.read_text(encoding="utf-8"))
    titles = meta["av"]
    total = len(titles)

    db = sqlite3.connect(str(DB))
    db.execute("PRAGMA journal_mode=WAL")
    have = {r[0] for r in db.execute(
        "SELECT id FROM titles WHERE category='hongkong' AND poster IS NOT NULL AND poster != ''")}

    found = fallback = skipped = 0
    for idx, t in enumerate(titles):
        slug = f"hk:cat3-{idx}"
        if slug in have:
            skipped += 1
            continue
        if args.limit and idx >= args.limit:
            break

        zh = t.get("zh") or ""
        year = t.get("year") or ""
        poster = ""
        score = -1.0
        try:
            results = douban_suggest(zh)
            best, score = best_match(zh, results, year)
            if best:
                poster = best["img"].replace("\\/", "/")
        except Exception as e:
            print(f"  {slug}: douban suggest failed: {e}", file=sys.stderr)

        if not poster:
            if not args.dry_run:
                poster = make_svg_card(slug, zh, year)
            fallback += 1
            print(f"[hkcat3-poster] {idx + 1}/{total} {zh} ({year}) -> SVG card (score={score:.2f})", flush=True)
        else:
            found += 1
            print(f"[hkcat3-poster] {idx + 1}/{total} {zh} ({year}) -> douban poster (score={score:.2f})", flush=True)

        if not args.dry_run:
            # doubanio hotlinks need a douban.com Referer, so the live /poster
            # proxy fetches with that referer; the DB keeps the raw URL.
            db.execute(
                "UPDATE titles SET poster = ?, background = ? WHERE id = ?",
                (poster, poster, slug),
            )
            db.commit()

        time.sleep(args.delay)

    db.close()
    print(f"posters: found={found} fallback={fallback} skipped={skipped}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

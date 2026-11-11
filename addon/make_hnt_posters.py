#!/usr/bin/env python3
"""Generate SVG title-card posters for hnt series that have no real poster.

Sukebei view pages for the missing series only carry the generic default
avatar as og:image (skipped by enrich_hentai.py), and hstream/hanime covers
do not exist for them. So for every hnt series whose rows all lack a poster,
we render a 512x768 SVG title card (gradient + clean series name), write it
to debrid-app/public/posters/hnt/<slug>.svg, and point every row of that
series at the served URL. Re-run after new hnt rows land; series that gain a
real poster (hanime CDN, hstream) are left untouched.
"""
import hashlib
import os
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQLITE = ROOT / "debrid-app" / "data" / "titles.sqlite"
OUT_DIR = ROOT / "debrid-app" / "public" / "posters" / "hnt"
BASE_URL = "https://javstremio.ddns.net"

# Same stop tokens as debrid-app/lib/titles-db.js SERIES_NAME_STOP, so the
# rendered title matches the series display name in the catalog.
SERIES_NAME_STOP = {
    "audio", "eng", "sub", "subs", "subbed", "dub", "dual", "vostfr", "final",
    "web", "dl", "hd", "h264", "h265", "avc", "x264", "aac", "flac",
    "1080p", "720p", "480p", "ov", "ova",
}


def series_display_name(series_key: str) -> str:
    if not series_key:
        return ""
    words = [w for w in series_key.split("-") if w and w.lower() not in SERIES_NAME_STOP]
    return " ".join(w[0].upper() + w[1:] for w in words) if words else series_key


def wrap(text: str, width: int = 16, max_lines: int = 4):
    words = text.split()
    lines: list[str] = []
    cur = ""
    for word in words:
        cand = (cur + " " + word).strip()
        if len(cand) <= width:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = word if len(word) <= width else word[: width - 1] + "…"
        if len(lines) == max_lines:
            break
    if cur and (not lines or lines[-1] != cur):
        lines.append(cur)
    return lines[:max_lines]


def svg_for(series_key: str, title: str) -> str:
    digest = hashlib.sha256(series_key.encode()).digest()
    hue = int.from_bytes(digest[:2], "big") % 360
    c1 = f"hsl({hue} 45% 16%)"
    c2 = f"hsl({(hue + 40) % 360} 55% 32%)"
    lines = wrap(title)
    n = len(lines)
    # center the block vertically around the poster's middle
    font = 34
    leading = 42
    top = 384 - (n * leading) // 2 + (leading - font) // 2
    text_elements = "\n".join(
        f'    <text x="256" y="{top + i * leading}" text-anchor="middle" '
        f'font-family="Arial, Helvetica, sans-serif" font-size="{font}" '
        f'font-weight="700" fill="#ffffff">{line}</text>'
        for i, line in enumerate(lines)
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="512" height="768" viewBox="0 0 512 768">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{c1}"/>
      <stop offset="100%" stop-color="{c2}"/>
    </linearGradient>
    <linearGradient id="frame" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#ffffff" stop-opacity="0.25"/>
      <stop offset="100%" stop-color="#ffffff" stop-opacity="0"/>
    </linearGradient>
  </defs>
  <rect width="512" height="768" fill="url(#bg)"/>
  <rect x="12" y="12" width="488" height="744" rx="14" fill="none" stroke="url(#frame)" stroke-width="2"/>
{text_elements}
  <text x="256" y="736" text-anchor="middle" font-family="Arial, Helvetica, sans-serif" font-size="16" fill="#ffffff" fill-opacity="0.55">javstreamio</text>
</svg>
'''


def main() -> int:
    db = sqlite3.connect(str(SQLITE))
    db.row_factory = sqlite3.Row
    rows = db.execute(
        """
        SELECT t.series_key AS key, t.name AS name, t.poster AS poster
        FROM titles t
        WHERE t.category LIKE 'hentai%' AND t.series_key != ''
        GROUP BY t.series_key
        HAVING COUNT(CASE WHEN t.poster IS NOT NULL AND t.poster != '' THEN 1 END) = 0
        ORDER BY MAX(t.posted_at) DESC
        """
    ).fetchall()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for r in rows:
        key = r["key"]
        if key.startswith("solo-"):
            title = r["name"] or f"Solo {key.split('-', 1)[1]}"
            title = re.sub(r"\s+", " ", title).strip()[:160]
        else:
            title = series_display_name(key) or f"hnt:{key}"
        slug = re.sub(r"[^A-Za-z0-9._-]", "_", key)
        svg_path = OUT_DIR / f"{slug}.svg"
        svg_path.write_text(svg_for(key, title), encoding="utf-8")
        url = f"{BASE_URL}/posters/hnt/{slug}.svg"
        db.execute(
            "UPDATE titles SET poster = ? WHERE series_key = ? AND (poster IS NULL OR poster = '')",
            (url, key),
        )
        written += 1
    db.commit()
    total = db.execute(
        "SELECT COUNT(*) FROM titles WHERE category LIKE 'hentai%' AND (poster IS NULL OR poster = '')"
    ).fetchone()[0]
    db.close()
    print(f"wrote {written} svg posters; hnt rows still missing poster: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

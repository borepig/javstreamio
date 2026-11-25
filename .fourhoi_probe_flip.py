#!/usr/bin/env python3
"""Probe the fourhoi covers for the reported "no image" families and flip
`fourhoi_missing=1` for any title whose fourhoi cover is not 200.

The live chain (addon-data.js applyPublicArt) prefers the fourhoi cover for
`dmm_missing` rows; when the cover 404s the response still sends that 404
URL, so Stremio shows no image. Flipping the flag makes the chain fall
through to the sehuatang forum poster via the /poster proxy (correct image,
guaranteed to load).
"""
import concurrent.futures
import re
import sqlite3
import sys
import urllib.error
import urllib.request

DB = "debrid-app/data/titles.sqlite"
FAMILIES = ["HEYZO", "SGKI", "FSDSS", "VDD", "NAMH", "NMSL", "KBR"]
LIMIT = 20  # latest N titles per family (covers the first catalog page)


def fourhoi_url(title_id):
    raw = re.sub(r"^jav:", "", title_id, flags=re.I).strip()
    m = re.match(r"^([A-Za-z]{2,})[-_](\d+)$", raw)
    if not m:
        return ""
    return f"https://fourhoi.com/{m.group(1).lower()}-{m.group(2)}/cover-n.jpg"


UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def probe(url):
    if not url:
        return 0
    try:
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", UA)
        # Note: fourhoi.com blocks the default Python-urllib UA (403) and a
        # Referer from fourhoi.com (403); a plain browser UA with no Referer
        # returns 200 for real covers and 404 for missing ones.
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.getcode()
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def main():
    db = sqlite3.connect(DB)
    cur = db.cursor()
    rows = []
    for fam in FAMILIES:
        rows += cur.execute(
            "SELECT id, fourhoi_missing FROM titles "
            "WHERE LOWER(id) LIKE ? ORDER BY posted_at DESC LIMIT ?",
            (f"jav:{fam.lower()}-%", LIMIT),
        ).fetchall()

    to_flip = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
        futs = {ex.submit(probe, fourhoi_url(r[0])): r for r in rows}
        for fut in concurrent.futures.as_completed(futs):
            r = futs[fut]
            status = fut.result()
            if status != 200 and r[1] != 1:
                to_flip.append(r[0])

    for tid in to_flip:
        cur.execute("UPDATE titles SET fourhoi_missing = 1 WHERE id = ?", (tid,))
    db.commit()

    print(f"probed {len(rows)} titles across {len(FAMILIES)} families (latest {LIMIT} each)")
    print(f"flipped fourhoi_missing=1 for {len(to_flip)} titles:")
    for tid in to_flip:
        print("  ", tid)
    return 0


if __name__ == "__main__":
    sys.exit(main())

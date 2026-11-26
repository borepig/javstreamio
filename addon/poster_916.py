#!/usr/bin/env python3
"""Normalise landscape ("printing") posters to a 9:16 centre crop.

User report (2026-11-26): many japan-photo (cat III) rows show landscape
"printing" posters instead of a proper portrait card, and the jav-censored /
jav-uncensored catalogs contain non-9:16 posters. The user confirmed 3:4 or
9:16 is fine — only landscape posters need normalising.

Scope:
- jav-censored / jav-uncensored: rows whose poster is on image.mgstage.com
  (landscape "pb_e_" prints, 840x568 / 1121x758).
- japan-photo: rows whose stored poster is neither pics.dmm.co.jp nor an
  already self-hosted URL; each such image is measured, and only the
  landscape ones (w > h) are cropped. Portrait (3:4) ones are kept.

For every affected row the current poster is downloaded (host-specific
headers — tu.djhdhs.us needs the sehuatang.org Referer, m-team.cc needs a
browser UA, imghost.biz has a broken TLS cert so SSL is verified off),
centre-cropped to 9:16 with ffmpeg, and written to
debrid-app/public/posters/<catdir>/<row_id>.jpg (served live at
https://javstremio.ddns.net/posters/<catdir>/<row_id>.jpg — the host is on
the app's public-art allow-list). The DB row's `poster` is pointed at that
URL.

Resume-safe: if the target JPG already exists on disk, only the DB write is
re-applied.

Usage:
    python3 addon/poster_916.py [--dry-run] [--limit N] [--cats japan-photo]
"""

import argparse
import os
import sqlite3
import ssl
import struct
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "debrid-app", "data", "titles.sqlite")
POSTERS_ROOT = os.path.join(ROOT, "debrid-app", "public", "posters")
LIVE_BASE = "https://javstremio.ddns.net/posters"

# category -> public/posters subdirectory (japancat3 already hosts the
# existing japan-photo AI posters).
CAT_DIRS = {
    "jav-censored": "javcensored",
    "jav-uncensored": "javuncensored",
    "japan-photo": "japancat3",
}


def jpeg_size(data):
    """Parse width/height from a JPEG's SOF marker (first 64 KB is enough)."""
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    n = len(data)
    while i < n - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD9 or marker in (0xDA, 0xDB):
            i += 2
            continue
        if marker == 0xDA:
            break
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        i += 2 + length
    return None


def png_size(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return w, h
    return None


def host_of(url):
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def fetch(url):
    """Download a poster with host-specific headers. Returns (bytes, err)."""
    host = host_of(url)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"}
    if host in ("tu.djhdhs.us", "tu.w4kmzkm.com"):
        headers["Referer"] = "https://sehuatang.org/"
    ctx = None
    if host == "www.imghost.biz":
        ctx = ssl._create_unverified_context()
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            return r.read(512 * 1024), None
    except Exception as e:
        return None, str(e)


def crop_to_916(src, dst):
    """Centre-crop a landscape image to 9:16 with ffmpeg; even dims for yuv420p."""
    with open(src, "rb") as f:
        data = f.read(512 * 1024)
    sz = jpeg_size(data) or png_size(data)
    if not sz:
        return False
    w, h = sz
    if w <= h:
        return False  # already portrait — keep as-is
    cw = (h * 9 // 16) // 2 * 2  # 9:16 width at full height, even
    if cw >= w:
        cw = w // 2 * 2
    cx = (w - cw) // 2
    vf = f"crop={cw}:{h}:{cx}:0"
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    cmd = ["ffmpeg", "-y", "-i", src, "-vf", vf, "-q:v", "3", dst]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return os.path.exists(dst) and os.path.getsize(dst) > 512


def iter_rows(db, cats, limit):
    rows = []
    for cat in cats:
        if cat in ("jav-censored", "jav-uncensored"):
            rows += db.execute(
                "SELECT id, poster FROM titles WHERE category=? AND poster LIKE ? ORDER BY posted_at DESC",
                (cat, "%image.mgstage.com%")).fetchall()
            # DMM posters for the label prefixes the user reported (2026-11-27)
            # showing landscape "NOW PRINTING" / wide posters.
            for pfx in ("JUKF", "YUME", "YSN", "YAG", "WFS", "ABF", "NASH"):
                rows += db.execute(
                    "SELECT id, poster FROM titles WHERE category=? AND id LIKE ? AND poster LIKE '%pics.dmm.co.jp%' "
                    "ORDER BY posted_at DESC",
                    (cat, f"jav:{pfx}%")).fetchall()
        elif cat == "japan-photo":
            rows += db.execute(
                "SELECT id, poster FROM titles WHERE category='japan-photo' "
                "AND poster != '' AND poster NOT LIKE '%pics.dmm.co.jp' AND poster NOT LIKE '%javstremio.ddns.net%' "
                "ORDER BY posted_at DESC").fetchall()
    if limit:
        rows = rows[:limit]
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--delay", type=float, default=0.2)
    ap.add_argument("--cats", default="japan-photo,jav-censored,jav-uncensored")
    args = ap.parse_args()
    cats = [c for c in args.cats.split(",") if c in CAT_DIRS]
    db = sqlite3.connect(DB)
    rows = iter_rows(db, cats, args.limit)
    print(f"[poster_916] {len(rows)} candidate rows to check")
    cropped = kept = missing = failed = 0
    for i, (row_id, poster) in enumerate(rows):
        cat = db.execute("SELECT category FROM titles WHERE id=?", (row_id,)).fetchone()[0]
        cat_dir = CAT_DIRS[cat]
        dst = os.path.join(POSTERS_ROOT, cat_dir, f"{row_id}.jpg")
        live_url = f"{LIVE_BASE}/{cat_dir}/{row_id}.jpg"
        if os.path.exists(dst):
            if not args.dry_run:
                db.execute("UPDATE titles SET poster=? WHERE id=?", (live_url, row_id))
                db.commit()
            kept += 1
            print(f"[poster_916] {i+1}/{len(rows)} {row_id}: re-applied DB write (JPG on disk)")
            continue
        data, err = fetch(poster)
        if data is None:
            failed += 1
            print(f"[poster_916] {i+1}/{len(rows)} {row_id}: fetch failed: {err[:80]}")
            if args.delay:
                time.sleep(0.5)
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        part = dst + ".part"
        with open(part, "wb") as f:
            f.write(data)
        ok = args.dry_run or crop_to_916(part, dst)
        if os.path.exists(part):
            os.remove(part)
        if ok:
            if not args.dry_run:
                db.execute("UPDATE titles SET poster=? WHERE id=?", (live_url, row_id))
                db.commit()
            sz = jpeg_size(data) or png_size(data)
            if sz and sz[0] > sz[1]:
                cropped += 1
                print(f"[poster_916] {i+1}/{len(rows)} {row_id}: cropped {sz[0]}x{sz[1]} -> 9:16")
            else:
                missing += 1
                print(f"[poster_916] {i+1}/{len(rows)} {row_id}: portrait {sz[0]}x{sz[1]} kept as-is")
        else:
            failed += 1
            print(f"[poster_916] {i+1}/{len(rows)} {row_id}: crop failed")
        if args.delay:
            time.sleep(args.delay)
    db.close()
    print(f"[poster_916] done: cropped={cropped} portrait-kept={missing} "
          f"reapplied={kept} failed={failed} (checked {len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

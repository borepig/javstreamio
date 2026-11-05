"""Backfill missing `name` values for iJavTorrent-sourced rows.

The original scrape stored some korean/chinese rows with an empty name
(the card's name span was absent on fetch, and the parser had no fallback).
This script re-fetches the iJavTorrent tag listing pages, resolves names
from the name div or the cover image's `alt` attribute, and updates only
the empty-name rows in the live SQLite DB.

Usage (from repo root):
    python3 addon/backfill_names.py [--categories korean chinese]
"""

import argparse
import re
import sqlite3
import sys
import time

# This script lives in addon/ next to build_addon.py, so the script
# directory is on sys.path and the import below resolves.
from build_addon import (
    IJA_BASE,
    IJA_CATEGORY_LABEL,
    IJA_TAGS,
    IJA_UA,
    TITLES_SQLITE,
    _ija_http_get,
    _ija_parse_listing,
    title_search_text,
)


def backfill_slugs(conn: sqlite3.Connection, category: str) -> dict:
    """Fill remaining empty names with the movie slug from the /movie/<id> redirect.

    These movies no longer appear in the tag listing (or their cards carry
    no name/alt), so the slug (e.g. 'md0044') becomes the name. Full titles
    can be layered on top later via web search.
    """
    import urllib.error
    import urllib.request
    rows = conn.execute(
        "SELECT id FROM titles WHERE category = ? AND (name IS NULL OR name = '')",
        (category,),
    ).fetchall()
    empty = [(r[0], r[0].split(":")[1].removeprefix("ijav-")) for r in rows]
    updated = failed = 0
    label = IJA_CATEGORY_LABEL[category]
    for sid, mid in empty:
        url = f"{IJA_BASE}/movie/{mid}"
        slug = ""
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": IJA_UA})
                # Capture the 301 Location without following it.
                class NoRedirect(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self, req, fp, code, msg, headers, newurl):
                        return None
                opener = urllib.request.build_opener(NoRedirect())
                try:
                    opener.open(req, timeout=30)
                    slug = "none"
                except urllib.error.HTTPError as e:
                    if e.code in (301, 302, 303, 307, 308):
                        loc = e.headers.get("Location") or ""
                        # Location is /movie/<slug>-<id>; the slug itself may
                        # contain hyphens (e.g. md-0338-201052), so split on
                        # the LAST hyphen to separate slug from the numeric id.
                        tail = loc.split("/movie/")[-1]
                        m = re.search(r".*-(\d+)$", tail)
                        if m and m.group(1) == mid:
                            slug = tail.rsplit("-", 1)[0]
                            if slug:
                                break
                except Exception:
                    pass
            except Exception:
                pass
        if not slug or slug == "none":
            failed += 1
            print(f"  {sid}: no slug resolved")
            continue
        title = {
            "id": sid,
            "name": slug,
            "description": f"{slug}\n\nSource: iJavTorrent ({label})",
            "genres": [label],
        }
        conn.execute(
            "UPDATE titles SET name = ?, description = ?, search_text = ? WHERE id = ?",
            (slug, title["description"], title_search_text(title), sid),
        )
        updated += 1
    conn.commit()
    left = conn.execute(
        "SELECT COUNT(*) FROM titles WHERE category = ? AND (name IS NULL OR name = '')",
        (category,),
    ).fetchone()[0]
    print(f"{category}: slug-backfilled {updated}/{len(empty)}; failed {failed}; empty remaining {left}")
    return {"category": category, "attempted": len(empty), "updated": updated, "failed": failed}


def backfill_category(conn: sqlite3.Connection, category: str, max_pages: int = 200) -> dict:
    tag = IJA_TAGS[category]
    label = IJA_CATEGORY_LABEL[category]
    prefix = {"korean": "kr", "chinese": "cn"}[category]

    empty_ids = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM titles WHERE category = ? AND (name IS NULL OR name = '')",
            (category,),
        )
    ]
    want = {sid.split(":")[1].removeprefix("ijav-") for sid in empty_ids}
    print(f"{category}: {len(empty_ids)} rows with empty name")

    found: dict[str, str] = {}
    page = 1
    consecutive_failures = 0
    while page <= max_pages and len(found) < len(empty_ids):
        url = f"{IJA_BASE}/tag/{tag}" + (f"?page={page}" if page > 1 else "")
        try:
            html = _ija_http_get(url)
            consecutive_failures = 0
        except Exception as e:
            consecutive_failures += 1
            print(f"page {page}: fetch failed ({e}); retrying", file=sys.stderr)
            if consecutive_failures >= 3:
                print("3 consecutive fetch failures; stopping", file=sys.stderr)
                break
            page += 1
            continue
        for item in _ija_parse_listing(html):
            if item["name"] and item["id"] in want:
                found[item["id"]] = item["name"]
        page += 1
        time.sleep(1.0)

    updated = 0
    for sid in empty_ids:
        mid = sid.split(":")[1].removeprefix("ijav-")
        name = found.get(mid, "")
        if not name:
            continue
        title = {
            "id": sid,
            "name": name,
            "description": f"{name}\n\nSource: iJavTorrent ({label})",
            "genres": [label],
        }
        conn.execute(
            "UPDATE titles SET name = ?, description = ?, search_text = ? WHERE id = ?",
            (name, title["description"], title_search_text(title), sid),
        )
        updated += 1
    conn.commit()
    still_missing = len(empty_ids) - updated
    print(f"{category}: updated {updated}/{len(empty_ids)} rows; {still_missing} still missing")
    return {"category": category, "empty": len(empty_ids), "updated": updated, "pages": page - 1}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--categories", nargs="+", default=["korean", "chinese"],
                    choices=list(IJA_TAGS.keys()))
    ap.add_argument("--slugs", action="store_true",
                    help="Fill remaining empty names with the movie slug from the /movie/<id> redirect")
    args = ap.parse_args()

    conn = sqlite3.connect(TITLES_SQLITE)
    for category in args.categories:
        if args.slugs:
            backfill_slugs(conn, category)
        else:
            backfill_category(conn, category)
    for category in args.categories:
        left = conn.execute(
            "SELECT COUNT(*) FROM titles WHERE category = ? AND (name IS NULL OR name = '')",
            (category,),
        ).fetchone()[0]
        print(f"{category}: empty names remaining = {left}")
    conn.close()


if __name__ == "__main__":
    main()

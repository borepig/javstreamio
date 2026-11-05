"""Enrich Chinese AV rows in the live SQLite from 3xplanet.net detail pages.

For each `cn:ijav-<id>` row (name = iJavTorrent code), fetch
`https://3xplanet.net/<code>/` and apply the full title (h1.tdb-title-text),
synopsis (xplanet-description), and publish date (<time datetime>).

Rows whose page 404s (or whose 品番 code field does not match the row's code)
are written to `addon/.chinese_websearch_pending.json` so the per-code
web-search fallback can pick them up.

Usage (from repo root):
    python3 addon/enrich_chinese.py
"""

import html as html_module
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from build_addon import TITLES_SQLITE, title_search_text

XPLANET_BASE = "https://3xplanet.net"
XPLANET_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
PENDING_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".chinese_websearch_pending.json")
DELAY = 0.8


def _xp_get(url: str) -> str:
    """GET with a few retries on transient 404/5xx."""
    for attempt in range(3):
        req = urllib.request.Request(url, headers={"User-Agent": XPLANET_UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (404, 500, 502, 503) and attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            raise
        except Exception:
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            raise


def _norm_code(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s)


def parse_detail(doc: str, code: str) -> dict | None:
    """Parse a 3xplanet detail page into (title, synopsis, spec_code, released)."""
    t = re.search(r'<h1 class="tdb-title-text">(.*?)</h1>', doc, re.S)
    if not t:
        return None
    title = html_module.unescape(_strip_tags(t.group(1))).strip()
    d = re.search(r'<div class="xplanet-description">\s*<p>(.*?)</p>', doc, re.S)
    synopsis = html_module.unescape(_strip_tags(d.group(1))).strip() if d else ""
    c = re.search(r"<dt>品番 / Code</dt>\s*<dd>(.*?)</dd>", doc, re.S)
    spec_code = html_module.unescape(c.group(1)).strip() if c else None
    tm = re.search(r'<time[^>]*datetime="(\d{4}-\d{2}-\d{2})', doc)
    released = f"{tm.group(1)}T00:00:00.000Z" if tm else ""
    return {"title": title, "synopsis": synopsis, "spec_code": spec_code, "released": released}


def enrich() -> dict:
    conn = sqlite3.connect(TITLES_SQLITE)
    rows = conn.execute(
        "SELECT id, name FROM titles WHERE category = 'chinese' ORDER BY posted_at DESC"
    ).fetchall()
    updated = mismatch = not_found = 0
    pending: list[str] = []
    t0 = time.time()
    for row_id, code in rows:
        code = (code or "").strip()
        if not code:
            not_found += 1
            pending.append(row_id)
            continue
        try:
            doc = _xp_get(f"{XPLANET_BASE}/{urllib.parse.quote(code, safe='')}/")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                not_found += 1
                pending.append(row_id)
                print(f"  {row_id} ({code}): 404 on 3xplanet; queued for web search")
                time.sleep(DELAY)
                continue
            raise
        parsed = parse_detail(doc, code)
        if not parsed:
            not_found += 1
            pending.append(row_id)
            print(f"  {row_id} ({code}): no h1 title on page; queued for web search")
            time.sleep(DELAY)
            continue
        if parsed["spec_code"] and _norm_code(parsed["spec_code"]) != _norm_code(code):
            mismatch += 1
            print(f"  {row_id}: 3xplanet code '{parsed['spec_code']}' != '{code}'; skipped")
            time.sleep(DELAY)
            continue
        description = parsed["title"] + "\n\n"
        if parsed["synopsis"]:
            description += parsed["synopsis"] + "\n\n"
        description += "Source: 3xplanet.net (Chinese AV)"
        title_dict = {"id": row_id, "name": parsed["title"], "genres": ["Chinese AV"]}
        search_text = title_search_text(title_dict)
        if parsed["released"]:
            conn.execute(
                "UPDATE titles SET name = ?, description = ?, search_text = ?, released = ? WHERE id = ?",
                (parsed["title"], description, search_text, parsed["released"], row_id),
            )
        else:
            conn.execute(
                "UPDATE titles SET name = ?, description = ?, search_text = ? WHERE id = ?",
                (parsed["title"], description, search_text, row_id),
            )
        updated += 1
        time.sleep(DELAY)
    conn.commit()
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(pending, f, indent=2)
    conn.close()
    print(f"chinese 3xplanet enrich: updated={updated}, code-mismatch={mismatch}, 404={not_found} "
            f"({time.time() - t0:.0f}s)")
    print(f"pending web-search list ({len(pending)} rows) -> {PENDING_FILE}")
    return {"updated": updated, "mismatch": mismatch, "not_found": not_found, "pending": len(pending)}


if __name__ == "__main__":
    enrich()

#!/usr/bin/env python3
"""Scrape the underhentai/Lulustream catalog and store the Lulustream
m3u8 token for hnt titles.

Pipeline:
  1. Scrape /page/N/ (N=1..MAX_PAGE) -> (slug, title) pairs.
  2. Match hnt titles (series name) against catalog titles (fuzzy).
  3. For a matched series, fetch /<slug>/ -> watch links -> /watch/?id&ep
     -> luluvdo iframe token; store in titles.sqlite `lulu_token`.

Usage:
  python3 addon/enrich_lulu.py --catalog        # step 1 only (writes .underhentai_catalog.json)
  python3 addon/enrich_lulu.py --map             # steps 2+3, writes lulu_token
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
import urllib.error

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
BASE = "https://www.underhentai.net"
CATALOG_JSON = os.path.join(os.path.dirname(__file__), ".underhentai_catalog.json")
DB = os.path.join(os.path.dirname(__file__), "..", "debrid-app", "data", "titles.sqlite")
MAX_PAGE = 60
DELAY = float(os.environ.get("LULU_DELAY", "0.4"))

# Nav slugs that appear on every page but are not series.
NAV_SLUGS = {"index", "releases", "uncensored", "top", "games", "random", "login"}


def get(url, referer=None, retries=3):
    headers = {"User-Agent": UA, "Accept": "text/html"}
    if referer:
        headers["Referer"] = referer
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            if e.code == 403 and attempt < retries - 1:
                time.sleep(2 + attempt * 2)
                continue
            raise
        except Exception:
            if attempt < retries - 1:
                time.sleep(1 + attempt)
                continue
            raise
    return ""


def scrape_catalog(max_page=MAX_PAGE):
    """Scrape all catalog pages; return list of {slug, title}."""
    catalog = []
    seen = set()
    for page in range(1, max_page + 1):
        url = f"{BASE}/page/{page}/" if page > 1 else f"{BASE}/"
        html = get(url)
        # Each series card: <a class="flex-col" href="/slug/"><div...><img...></div><h3>Title</h3></a>
        card_re = re.compile(r'<a class="flex-col" href="/([a-z0-9-]+/)"[^>]*>(.*?)<h3>(.*?)</h3>', re.S)
        for m in card_re.finditer(html):
            slug = m.group(1).strip("/")
            title = m.group(3).strip()
            if slug in NAV_SLUGS or slug in seen:
                continue
            seen.add(slug)
            catalog.append({"slug": slug, "title": title})
        if page % 10 == 0:
            print(f"  catalog page {page}/{max_page}: {len(catalog)} series", flush=True)
        time.sleep(DELAY)
    return catalog


def load_catalog():
    if os.path.exists(CATALOG_JSON):
        with open(CATALOG_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_catalog(catalog):
    with open(CATALOG_JSON, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=1)


# ---------- title matching ----------

STOP = re.compile(r"\s*(the|a|an|of|and|to|in|on|for|no|de|la|le|du|und)\s*")

def norm(s):
    s = STOP.sub(" ", (s or "").lower())
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def tokenize(s):
    return [t for t in norm(s).split(" ") if t]


def series_from_hnt_name(name):
    """Extract the series title from a Nyaa-style hnt name.

    Examples:
      '[HentaiHub] Harem Camp S01E07 1080p UNCENSORED OV WEB-DL AAC' -> 'Harem Camp'
      '[Patreon] HentaiSanctuary01 - Kasane Teto' -> 'HentaiSanctuary01'
    """
    s = name or ""
    # strip [Group] prefix
    s = re.sub(r"^\[[^\]]+\]\s*", "", s)
    # strip episode/season markers and trailing quality tokens
    s = re.sub(r"\s+S\d+E\d+\b.*$", "", s)
    s = re.sub(r"\s+1080p.*$", "", s)
    # take the part before a ' - ' separator (series name before the dash)
    if " - " in s:
        s = s.split(" - ")[0]
    return s.strip()


def english_title_from_hnt_name(name):
    """Extract the English title from the trailing parentheses in an hnt name.

    hnt names carry an English title in parentheses, e.g.
    "... (Taisho Era Contract Marriage: The Substitute Bride and a Soldier's Fierce Love, Premium Edition)".
    The English title matches the English catalog titles far better than the
    romanized-Japanese series name, so it is used as a second matching candidate.
    """
    m = re.search(r"\(([^)]*)\)\s*$", name or "")
    if not m:
        return ""
    t = m.group(1)
    # Strip the trailing edition/subtitle tag after the last comma.
    t = re.split(r",\s*(?:Premium Edition|English-Sub|VOSTFR|VOSTEN|VOSTFR)", t)[0]
    # Unescape common HTML entities so apostrophes match the catalog titles.
    t = t.replace("&#39;", "'").replace("&amp;", "&").replace("&quot;", '"')
    return t.strip()


def extra_english_title(name):
    """Extract an English title from other name formats.

    Handles the Doomdos format (trailing `[KAMUI He's Behind You]` bracket) and the
    pipe-separated format (`... | try all my might ... DIFFERENT WORLD - 07 [Raw]`).
    """
    s = (name or "").replace("&#39;", "'")
    # 1) Trailing bracket [English Title] (Doomdos).
    m = re.search(r"\[([A-Z][^][]*)\]\s*$", s)
    if m:
        return m.group(1).strip()
    # 2) Pipe separator: text after the last ' | '.
    if " | " in s:
        t = s.rsplit(" | ", 1)[1]
        t = re.sub(r"\s*-\s*\d+\s*(?:\[Raw\])?\s*$", "", t)
        return t.strip()
    return ""


def match_title(name, catalog):
    """Fuzzy-match the hnt series name against catalog titles (cross-language).

    Tries the romanized-Japanese series name, the English title from the trailing
    parentheses, and the English title from a trailing bracket / pipe, keeping the
    best (item, score) across all candidates.
    """
    import difflib
    candidates = []
    series = series_from_hnt_name(name)
    if series:
        candidates.append(series)
    eng = english_title_from_hnt_name(name)
    if eng:
        candidates.append(eng)
    extra = extra_english_title(name)
    if extra:
        candidates.append(extra)
    if not candidates:
        return None, 0.0
    best = None
    best_score = 0.0
    for cand in candidates:
        s_norm = norm(cand)
        st = tokenize(cand)
        for item in catalog:
            c_norm = norm(item["title"])
            ratio = difflib.SequenceMatcher(None, s_norm, c_norm).ratio()
            ct = tokenize(item["title"])
            inter = set(st) & set(ct)
            union = set(st) | set(ct)
            tok = len(inter) / len(union) if union else 0.0
            score = max(ratio, tok)
            if score > best_score:
                best_score = score
                best = item
    return (best, best_score)


def open_db():
    path = os.path.normpath(DB)
    con = sqlite3.connect(path)
    cur = con.cursor()
    # Ensure lulu_token column exists.
    cols = [r[1] for r in cur.execute("PRAGMA table_info(titles)").fetchall()]
    if "lulu_token" not in cols:
        cur.execute("ALTER TABLE titles ADD COLUMN lulu_token TEXT DEFAULT ''")
        con.commit()
    return con


def resolve_token_for_row(cur, row_id):
    """For a matched series, fetch the series page -> watch links -> watch page -> luluvdo token."""
    row = cur.execute("SELECT name FROM titles WHERE id=?", (row_id,)).fetchone()
    if not row:
        return None
    catalog = load_catalog()
    item, score = match_title(row[0], catalog)
    if item is None or score < 0.20:
        return None
    slug = item["slug"]
    # series page -> episode id
    html = get(f"{BASE}/{slug}/", referer=f"{BASE}/")
    wm = re.search(r"/watch/\?id=(\d+)&ep=", html.replace("&amp;", "&"))
    if not wm:
        return None
    series_id = wm.group(1)
    # Determine the episode number from the hnt name (SxxExx).
    em = re.search(r"S(\d+)E(\d+)", (row[0] or "").upper())
    ep = int(em.group(2)) if em else 0
    watch = get(f"{BASE}/watch/?id={series_id}&ep={ep}", referer=f"{BASE}/{slug}/")
    im = re.search(r'<iframe[^>]*src=["\']https://luluvdo\.com/e/([a-z0-9]+)["\']', watch)
    return im.group(1) if im else None


def do_map(con):
    cur = con.cursor()
    catalog = load_catalog()
    if not catalog:
        print("No catalog found; run --catalog first.")
        return
    rows = cur.execute("SELECT id, name FROM titles WHERE id LIKE 'hnt:%' AND COALESCE(lulu_token,'')=''").fetchall()
    print(f"Mapping {len(rows)} hnt rows (catalog {len(catalog)} series)")
    done = 0
    for i, (row_id, name) in enumerate(rows):
        tok = resolve_token_for_row(cur, row_id)
        if tok:
            cur.execute("UPDATE titles SET lulu_token=? WHERE id=?", (tok, row_id))
            done += 1
            print(f"  [{i+1}/{len(rows)}] {row_id} -> {tok}", flush=True)
        time.sleep(DELAY)
        if (i + 1) % 25 == 0:
            con.commit()
    con.commit()
    print(f"Resolved {done}/{len(rows)} lulu tokens.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", action="store_true", help="Scrape the underhentai catalog to JSON")
    ap.add_argument("--map", action="store_true", help="Match hnt titles and store lulu_token in SQLite")
    ap.add_argument("--pages", type=int, default=MAX_PAGE)
    args = ap.parse_args()
    if args.catalog:
        cat = scrape_catalog(args.pages)
        save_catalog(cat)
        print(f"Scraped {len(cat)} series -> {CATALOG_JSON}")
    if args.map:
        con = open_db()
        do_map(con)
        con.close()
    if not args.catalog and not args.map:
        ap.print_help()


if __name__ == "__main__":
    main()

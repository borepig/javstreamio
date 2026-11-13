#!/usr/bin/env python3
"""HK Cat III magnet search — wiki-info first, then Chinese-name magnets.

Passes 1–3 (hk_cat3_magnet_search{,2}.py) built queries from the English
name when one existed; that pulled in irrelevant English movies — the DB now
shows 146/172 titles sharing a magnet hash, so most of those magnets are
wrong. This job implements the user's method:

1. INFO: SearXNG search "<zh> <year>"; find the zh.wikipedia.org page;
   fetch it and extract the canonical title, cast (主演/演員), poster
   (og:image, upload.wikimedia.org) and the English alias (英文名).
2. VARIANTS: zh as-is + simplified + traditional (zhconv s2t/t2s), plus
   the wiki English alias. The json "en" field is skipped (unreliable —
   e.g. "Eternal Evil of Asia" is stamped on many unrelated titles).
3. MAGNETS: for each variant, SearXNG "<variant> <year> 1080p 粤語";
   fetch the top result pages and extract magnet:?xt=urn:btih: links.
4. SCORE: +2 when the candidate text contains a name variant, +3 hi-res,
   +3 Cantonese original, -3 dubbing.
5. WRITE: overwrite magnet + info_hash for every title (old magnets are
   suspect), plus wiki poster / cast / exact release date when found.

Resume note: unlike pass 1/2, this job always re-searches (the existing
magnets are the very thing being replaced).

Usage:
    python3 addon/hk_cat3_wiki_magnet_search.py [--limit N] [--delay SEC] [--dry-run] [--no-wiki]
"""

import argparse
import html as html_mod
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

try:
    import zhconv
except ImportError:
    zhconv = None

ROOT = Path(__file__).resolve().parent.parent
META_JSON = ROOT / "addon" / "hk_cat3_av.json"
DB = ROOT / "debrid-app" / "data" / "titles.sqlite"
SEARXNG = os.environ.get("SEARXNG_URL", "http://192.168.68.83:8888")
AVGOOD = "https://avgood.com"

MAGNET_RE = re.compile(r'magnet:\?xt=urn:btih:([A-Fa-f0-9]{40})')
HASH_RE = re.compile(r'urn:btih:([A-Fa-f0-9]{40})')
HI_RES_RE = re.compile(r"1080p|2160p|2160|4k|bluray|blu-ray|remux|x264|x265|hevc|avc|高清|hd\b", re.I)
CANTONESE_RE = re.compile(r"粤語|粤语|cantonese|原聲|原音|original\s*audio", re.I)
DUB_RE = re.compile(r"國語|国语|mandarin|eng\s*dub|english\s*dub|雙語|双语|dubbed|雙語版|國語版", re.I)
WIKI_URL_RE = re.compile(r'zh\.wikipedia\.org', re.I)


def to_simplified(s: str) -> str:
    return zhconv.convert(s, "t2s") if zhconv else s


def to_traditional(s: str) -> str:
    return zhconv.convert(s, "s2t") if zhconv else s


def search_sxng(query: str, num: int = 10) -> list[dict]:
    """One SearXNG JSON search. Returns results (url/title/content).

    Retries transport errors with exponential backoff, and retries once
    after 5s when 0 results are returned (the local SearXNG upstream
    engines — google cse quota, ddg CAPTCHA — intermittently go dark)."""
    url = f"{SEARXNG}/search?q={urllib.parse.quote(query)}&format=json&pageno=1"
    backoff = 15
    while True:
        try:
            req = Request(url, headers={"User-Agent": "javstreamio-hkcat3wiki/1.0"})
            with urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
            results = data.get("results") or []
            if results:
                return results
            if search_sxng._retried is None:
                print(f"    sxng 0 results (upstream engines rate-limited); retry in 5s")
                time.sleep(5)
                search_sxng._retried = True
                continue
            return []
        except Exception as e:
            print(f"    sxng error: {e}; retry in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)
search_sxng._retried = None


def fetch_page(url: str) -> str:
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (javstreamio)"})
        with urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        return ""


def fetch_page_magnets(url: str) -> list[tuple[str, str]]:
    html = fetch_page(url)
    if not html:
        return []
    magnets = []
    for m in MAGNET_RE.finditer(html):
        magnets.append((m.group(0), m.group(1)))
    if not magnets:
        for m in HASH_RE.finditer(html):
            h = m.group(1)
            magnets.append((f"magnet:?xt=urn:btih:{h}", h))
    return magnets


def fetch_wiki_page(zh: str) -> dict:
    """Direct fetch of the zh-hk Wikipedia page for a movie name
    (deterministic fallback when SearXNG is rate-limited or returns
    nothing). Tries the zh-hk (traditional) and plain /wiki/ URLs."""
    for path in (f"/zh-hk/{urllib.parse.quote(zh)}", f"/wiki/{urllib.parse.quote(zh)}"):
        url = "https://zh.wikipedia.org" + path
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0 (javstreamio)"})
            with urlopen(req, timeout=20) as r:
                html = r.read().decode("utf-8", "replace")
            return parse_wiki_page(html)
        except Exception:
            continue
    return {}


def parse_wiki_page(html: str) -> dict:
    """Extract canonical title, poster, cast, synopsis (劇情簡介), English
    alias, release date from a zh.wikipedia.org page. Returns {} when the
    html is empty. Metadata stays in Chinese (per user instruction)."""
    out = {"title": "", "poster": "", "cast": [], "en_alias": "", "released": "", "synopsis": ""}
    if not html:
        return out
    m = re.search(r'<title>(.*?)</title>', html, re.S)
    if m:
        out["title"] = m.group(1).split(" - ")[0].strip()
    m = re.search(r'property="og:image"\s+content="([^"]+)"', html)
    if m:
        out["poster"] = html_mod.unescape(m.group(1)).strip()
    m = re.search(r'(?:英文名|英名)\s*[：:]?\s*([A-Z][A-Za-z0-9 .,&\'-]{2,60})', html)
    if m:
        out["en_alias"] = m.group(1).strip()
    if not out["en_alias"]:
        # cross-language links: the English title is the en.wikipedia slug
        m = re.search(r'href="https://en\.wikipedia\.org/wiki/([^"?]+)"', html)
        if m:
            out["en_alias"] = m.group(1).replace("_", " ")
    m = re.search(r'<th[^>]*>(?:主演|演員|演员|演員表|主創人員)</th>\s*<td[^>]*>(.*?)</td>', html, re.S)
    if m:
        out["cast"] = [a.strip() for a in re.findall(r'<a[^>]*>([^<]+)</a>', m.group(1))]
    m = re.search(r'<th[^>]*>(?:上映日期|上映|發行日期|上映時間|上映時間)</th>\s*<td[^>]*>(.*?)</td>', html, re.S)
    if m:
        d = re.search(r'(\d{4})-(\d{2})-(\d{2})', m.group(1))
        if d:
            out["released"] = f"{d.group(1)}-{d.group(2)}-{d.group(3)}"
    # 劇情簡介 section body (heading id is traditional 劇情簡介; TOC text is
    # simplified 剧情简介 — match the heading by its section id or text).
    m = re.search(r'<div class="mw-heading mw-heading2"><h2 id="劇情簡介"', html)
    if m:
        rest = html[m.end():]
        nxt = re.search(r'<div class="mw-heading', rest)
        seg = rest[:nxt.start()] if nxt else rest[:4000]
        paras = re.findall(r'<p>(.*?)</p>', seg, re.S)
        text = " ".join(
            re.sub(r"<[^>]+>", "", html_mod.unescape(p)).strip() for p in paras)
        out["synopsis"] = re.sub(r"\s+", " ", text).strip()
    return out


AVGOOD_RESULT_RE = re.compile(r'<a href="/c/(\d+)\.html"[^>]*>(.*?)</a>', re.S)
AVGOOD_RELEASED_RE = re.compile(r'发行日期:(\d{4}-\d{2}-\d{2})')


def avgood_search(zh: str) -> list[tuple[str, str]]:
    """AvGood server-rendered search (https://avgood.com/c/s/?q=<name>).

    The results page is static HTML — parse the result cards and return
    [(detail_id, title)]."""
    url = f"{AVGOOD}/c/s/?q={urllib.parse.quote(zh)}"
    page = fetch_page(url)
    out = []
    body = page[page.find("<body"):] if page else ""
    for m in AVGOOD_RESULT_RE.finditer(body):
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        out.append((m.group(1), title))
    return out


AVGOOD_URL_RE = re.compile(r'avgood\.com/c/(\d+)\.html')


def avgood_ids_via_sxng(zh: str) -> list[str]:
    """User method: SearXNG search "<zh> avgood" -> AvGood detail ids.

    Engine-independent fallback is the AvGood search page (avgood_search),
    used when SearXNG is rate-limited or returns nothing."""
    results = search_sxng(f"{zh} avgood")
    ids = []
    for r in results[:10]:
        m = AVGOOD_URL_RE.search(r.get("url") or "")
        if m and m.group(1) not in ids:
            ids.append(m.group(1))
    return ids


def avgood_detail(id: str) -> dict:
    """Fetch the AvGood detail page /c/<id>.html; extract the magnet,
    release date (meta description 发行日期) and the /remote/pic poster."""
    page = fetch_page(f"{AVGOOD}/c/{id}.html")
    out = {"magnet": "", "hash": "", "released": "", "poster": ""}
    if not page:
        return out
    m = MAGNET_RE.search(page)
    if m:
        out["magnet"] = m.group(0)
        out["hash"] = m.group(1)
    d = AVGOOD_RELEASED_RE.search(page)
    if d:
        out["released"] = d.group(1)
    p = re.search(r'(/remote/pic/[^"\s]+)', page)
    if p:
        out["poster"] = AVGOOD + p.group(1)
    return out


def score_candidate(text: str, variants: list[str]) -> int:
    low = text.lower()
    s = 0
    if any(v and v.lower() in low for v in variants):
        s += 2
    if HI_RES_RE.search(text):
        s += 3
    if CANTONESE_RE.search(text):
        s += 3
    if DUB_RE.search(text):
        s -= 3
    return s


def build_variants(t: dict, wiki: dict) -> list[str]:
    zh = t.get("zh", "")
    vs = [zh, to_simplified(zh), to_traditional(zh)]
    if wiki.get("en_alias"):
        vs.append(wiki["en_alias"].strip())
    seen = set()
    out = []
    for v in vs:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def open_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB))
    db.execute("PRAGMA journal_mode=WAL")
    return db


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-wiki", action="store_true",
                    help="skip the wiki info step; use Chinese name variants only")
    ap.add_argument("--no-avgood", action="store_true",
                    help="skip the AvGood search + detail-page magnets")
    args = ap.parse_args()

    if not zhconv:
        print("warning: zhconv not installed — running without trad/simp conversion", file=sys.stderr)

    meta = json.loads(META_JSON.read_text(encoding="utf-8"))
    titles = meta["av"]
    total = len(titles)
    if args.limit:
        titles = titles[:args.limit]

    db = open_db()
    found = nomag = wikihit = 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    for idx, t in enumerate(titles):
        slug = f"hk:cat3-{idx}"
        zh = t.get("zh", "")
        year = t.get("year", "")

        # 1) info search + wiki page (with deterministic direct-fetch
        #    fallback when SearXNG is rate-limited or finds nothing)
        wiki = {}
        if not args.no_wiki:
            info_results = search_sxng(f"{zh} {year}")
            wiki_url = next((r.get("url") or "" for r in info_results
                             if WIKI_URL_RE.search(r.get("url") or "")), "")
            if wiki_url:
                wiki = parse_wiki_page(fetch_page(wiki_url))
                wikihit += 1
            if not wiki.get("title"):
                fallback = fetch_wiki_page(zh)
                if fallback.get("title"):
                    wikihit += 1
                wiki = fallback
            time.sleep(0.3)

        # 2) name variants (Chinese only + wiki English alias)
        variants = build_variants(t, wiki)

        # 3) AvGood: SearXNG "<zh> avgood" search (user method) + the
        #    engine-independent AvGood search page as fallback. Collect the
        #    candidate detail pages, fetch their magnets, and pick the
        #    highest-quality one (hi-res/Cantonese +, dubbing -).
        avgood = {"magnet": "", "hash": "", "released": "", "poster": ""}
        avgood_src = ""
        if not args.no_avgood:
            cands = {}
            for rid in avgood_ids_via_sxng(zh):
                cands[rid] = ""
            for rid, ttl in avgood_search(zh):
                if any(v and v in ttl for v in variants):
                    cands.setdefault(rid, ttl)
            best_avgood = None
            for rid in list(cands)[:6]:
                det = avgood_detail(rid)
                if det["magnet"]:
                    s = score_candidate(cands[rid], variants)
                    if best_avgood is None or s > best_avgood[0]:
                        best_avgood = (s, rid, det)
                time.sleep(0.3)
            if best_avgood:
                avgood = best_avgood[2]
                avgood_src = f"avgood /c/{best_avgood[1]}.html"

        best = None
        if avgood["magnet"]:
            best = (best_avgood[0], avgood["magnet"], avgood["hash"], avgood_src)
        else:
            for v in variants[:4]:
                results = search_sxng(f"{v} {year} 1080p 粤語")
                for res in results[:6]:
                    url = (res.get("url") or "").strip()
                    if not url:
                        continue
                    cand_name = f"{res.get('title','')} {res.get('content','')}"
                    for magnet, h in fetch_page_magnets(url):
                        s = score_candidate(cand_name, variants)
                        if best is None or s > best[0]:
                            best = (s, magnet, h, url)
                    time.sleep(0.3)

        if best:
            found += 1
            desc = wiki.get("synopsis") or ""
            if not desc:
                desc_parts = [zh]
                if wiki.get("en_alias"):
                    desc_parts.append(wiki["en_alias"])
                desc_parts.append(f"(Wikipedia 香港三级片列表 {year})")
                desc = " ".join(desc_parts)
            if avgood["released"]:
                released = f"{avgood['released']}T00:00:00.000Z"
            elif wiki.get("released"):
                released = f"{wiki['released']}T00:00:00.000Z"
            else:
                released = f"{year}-01-01T00:00:00.000Z"
            search_text = " ".join(x for x in [zh, year, wiki.get("en_alias", "")] if x).lower()
            cast_json = json.dumps(wiki.get("cast") or [], ensure_ascii=False)
            poster = wiki.get("poster") or avgood.get("poster", "")
            if not args.dry_run:
                db.execute(
                    """INSERT INTO titles (id, name, description, released,
                                         magnet, info_hash, posted_at, search_text,
                                         poster, cast_json, category, meta_rank)
                       VALUES (?,?,?,?,?,?,?,?, ?, ?, 'hongkong', 0)
                       ON CONFLICT(id) DO UPDATE SET
                         name=excluded.name,
                         description=excluded.description,
                         released=excluded.released,
                         magnet=excluded.magnet,
                         info_hash=excluded.info_hash,
                         posted_at=excluded.posted_at,
                         search_text=excluded.search_text,
                         poster=CASE WHEN excluded.poster != '' THEN excluded.poster ELSE titles.poster END,
                         cast_json=CASE WHEN excluded.cast_json != '[]' THEN excluded.cast_json ELSE titles.cast_json END
                    """,
                    (slug, zh, desc, released, best[1],
                     best[2], now, search_text, poster, cast_json),
                )
            db.commit()
            print(f"[hkcat3wiki] {idx + 1}/{total} {zh} ({year}) -> magnet score={best[0]} wiki={'yes' if wiki.get('title') else 'no'} src={best[3]}")
        else:
            nomag += 1
            print(f"[hkcat3wiki] {idx + 1}/{total} {zh} ({year}) -> no magnet found (wiki={'yes' if wiki.get('title') else 'no'})")
        if args.delay:
            time.sleep(args.delay)

    db.close()
    print(f"done: found={found} no_magnet={nomag} wiki_pages={wikihit} of {min(len(meta['av']), args.limit or len(meta['av']))} titles")
    return 0


if __name__ == "__main__":
    sys.exit(main())

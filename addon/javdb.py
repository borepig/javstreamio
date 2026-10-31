"""Enrich Stremio meta with full details from JavDB (javdb.com).

For each JAV title number this module:
  1. GET https://javdb.com/search?q=<number>  -> find the /v/<id> detail link
  2. GET https://javdb.com/v/<id>          -> parse the panel-block fields
  3. Merge JavDB fields into the base meta (cast, genres, runtime, released, maker, series, rating)

If JavDB is unreachable or the number is not found, the base (scraped) meta is returned
unchanged so the addon still works.

A local cache file (addon/.javdb_cache.json) avoids re-fetching on rebuilds.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

JAVDB = "https://javdb.com"
JAV321 = "https://www.jav321.com"
ROOT = Path(__file__).resolve().parent.parent
CACHE_FILE = Path(__file__).resolve().parent / ".javdb_cache.json"
JAV321_CACHE_FILE = Path(__file__).resolve().parent / ".jav321_cache.json"
DELAY = 0.4          # seconds between JavDB requests (be polite to the site)
RETRIES = 3          # retry attempts on transient request failures
RETRY_BACKOFF = 1.0  # base backoff seconds, doubled each retry
JAV_CODE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])([A-Z]{2,10})[-_](\d{2,6})(?![A-Za-z0-9])"
)

_SESSION: Optional[requests.Session] = None
_CACHE: Optional[dict] = None


def _session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        _SESSION.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        _SESSION.headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        _SESSION.headers["Accept-Language"] = "zh-TW,zh,en;q=0.8"
    return _SESSION


def _get(url: str, timeout: int = 20) -> Optional[str]:
    last_err = None
    for attempt in range(RETRIES):
        try:
            r = _session().get(url, timeout=timeout, allow_redirects=True)
            if r.status_code == 200:
                time.sleep(DELAY)
                return r.text
            last_err = f"HTTP {r.status_code}"
            # rate limited: honor Retry-After, otherwise wait longer
            if r.status_code == 429:
                ra = r.headers.get("Retry-After")
                wait = float(ra) if ra and ra.isdigit() else 10.0
                time.sleep(wait)
            else:
                time.sleep(RETRY_BACKOFF * (2 ** attempt))
        except requests.RequestException as e:
            last_err = str(e)
            time.sleep(RETRY_BACKOFF * (2 ** attempt))
    if last_err:
        print(f"javdb: GET {url} failed after {RETRIES} tries: {last_err}")
    return None


def _load_cache() -> dict:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    if CACHE_FILE.exists():
        try:
            _CACHE = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            _CACHE = {}
    else:
        _CACHE = {}
    return _CACHE


def _save_cache(cache: dict) -> None:
    global _CACHE
    _CACHE = cache
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _normalize_runtime(raw: str) -> str:
    """'135 分鍾' -> '135m'; '48分' -> '48m'."""
    m = re.search(r"(\d+)\s*(?:分鍾|分|min|m)", raw or "", re.I)
    return f"{m.group(1)}m" if m else ""


def _parse_detail(html: str) -> dict:
    """Parse JavDB detail page panel-blocks into a field dict."""
    soup = BeautifulSoup(html, "html.parser")
    fields = {}
    for div in soup.select("div.panel-block"):
        strong = div.find("strong")
        value = div.find("span", class_="value")
        if not (strong and value):
            continue
        label = strong.get_text(strip=True).rstrip(":：")
        if label == "日期":
            fields["released"] = value.get_text(strip=True)
        elif label == "時長":
            fields["runtime"] = _normalize_runtime(value.get_text(" ", strip=True))
        elif label == "類別":
            fields["genres"] = [a.get_text(strip=True) for a in value.select("a")] or [value.get_text(" ", strip=True)]
        elif label == "演員":
            fields["cast"] = [a.get_text(strip=True) for a in value.select("a")] or [value.get_text(" ", strip=True)]
        elif label == "片商":
            fields["maker"] = value.get_text(" ", strip=True)
        elif label == "發行":
            fields["distributor"] = value.get_text(" ", strip=True)
        elif label == "系列":
            fields["series"] = value.get_text(" ", strip=True)
        elif label == "評分":
            m = re.search(r"(\d+(?:\.\d+)?)", value.get_text(" ", strip=True))
            if m:
                fields["rating"] = m.group(1)
        elif label == "番號":
            fields["number"] = value.get_text(strip=True)
        elif label in ("導演", "監督"):
            names = [a.get_text(strip=True) for a in value.select("a")] or [value.get_text(" ", strip=True)]
            fields["director"] = [n for n in names if n]
    title = _parse_title(soup)
    if title:
        fields["title"] = title
    summary = _parse_summary(soup)
    if summary:
        fields["summary"] = summary
    cover = _parse_cover(soup)
    if cover:
        fields["poster"] = cover
        fields["fanart"] = cover
    return fields


def _parse_title(soup: BeautifulSoup) -> str:
    heading = soup.select_one("h2.title") or soup.select_one("strong.current-title")
    if not heading:
        return ""
    text = heading.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text)


def _parse_summary(soup: BeautifulSoup) -> str:
    for meta in soup.select('meta[property="og:description"], meta[name="description"]'):
        content = (meta.get("content") or "").strip()
        if len(content) >= 20:
            return content
    for sel in (".panel.hidden-xs .panel-block", "p.movie-description", ".plot", ".description"):
        node = soup.select_one(sel)
        if not node:
            continue
        text = node.get_text(" ", strip=True)
        if len(text) >= 20:
            return text
    return ""


def _norm_code(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def cache_fits(number: str, fields: dict | None) -> bool:
    """Ignore JavDB cache rows whose 番號 does not match the query (western false hits)."""
    if not fields:
        return False
    cached = _norm_code(fields.get("number") or "")
    want = _norm_code(number)
    if not want:
        return False
    if not cached:
        return bool(JAV_CODE_RE.search(number or ""))
    return cached == want or cached in want or want in cached


def _parse_cover(soup: BeautifulSoup) -> str:
    """JavDB box cover on c0.jdbstatic.com — public, not stored on Vercel."""
    img = soup.select_one("img.video-cover") or soup.select_one(".movie-list .cover img")
    if not img:
        return ""
    src = (img.get("src") or img.get("data-src") or "").strip()
    if src.startswith("//"):
        src = "https:" + src
    if src.startswith("https://") and "jdbstatic.com" in src:
        return src
    return ""


def javdb_meta(number: str, use_cache: bool = True, allow_network: bool = True) -> Optional[dict]:
    """Fetch and parse JavDB details for a title number. Returns a field dict, or None if unavailable."""
    number = (number or "").strip()
    if not number:
        return None

    cache = _load_cache() if use_cache else {}
    if use_cache and number in cache:
        fields = cache[number]
        return fields if cache_fits(number, fields) else None
    if not allow_network:
        return None

    search_html = _get(f"{JAVDB}/search?q={number}")
    if not search_html:
        return None
    m = re.search(r'href="(/v/[^"]+)"', search_html)
    if not m:
        return None
    detail_url = JAVDB + m.group(1)
    detail_html = _get(detail_url)
    if not detail_html:
        return None

    fields = _parse_detail(detail_html)
    if fields and cache_fits(number, fields):
        if use_cache:
            cache[number] = fields
            _save_cache(cache)
        return fields
    return None


def compose_description(rec: dict | None, fields: dict | None, fallback: str = "") -> str:
    """Plot / official title first, then JavDB facts. Forum blurbs count as the summary."""
    rec = rec or {}
    fields = fields or {}
    parts: list[str] = []
    summary = (fields.get("summary") or fields.get("title") or "").strip()
    forum = (rec.get("title") or "").strip()
    if summary:
        parts.append(summary)
    if forum and forum not in summary:
        parts.append(forum)
    facts: list[str] = []
    # Cast, genres, runtime, rating, and release belong on the Meta object
    # (`cast` / `links` / `runtime` / `imdbRating`). Do not dump them into
    # the plot — Stremio already shows those fields on the detail page.
    if fields.get("maker"):
        facts.append("片商: " + fields["maker"])
    if fields.get("distributor"):
        facts.append("發行商: " + fields["distributor"])
    if fields.get("series"):
        facts.append("系列: " + fields["series"])
    if rec.get("影片容量"):
        facts.append("容量: " + rec["影片容量"])
    if rec.get("是否有码"):
        facts.append("有码/无码: " + rec["是否有码"])
    if facts:
        parts.append("\n".join(facts))
    text = "\n\n".join(part for part in parts if part).strip()
    return text or fallback


def _dmm_cid(number: str) -> str:
    raw = (number or "").strip()
    match = re.match(r"^([A-Za-z]{2,})[-_]?0*(\d+)$", raw)
    if not match:
        return re.sub(r"[^A-Za-z0-9]", "", raw).lower()
    return match.group(1).lower() + match.group(2).zfill(5)


def _parse_jav321(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    panel = soup.select_one(".panel")
    if not panel:
        return {}
    fields: dict = {}
    lines = [ln.strip() for ln in panel.get_text("\n").splitlines() if ln.strip()]
    if lines:
        fields["title"] = lines[0]
        fields["summary"] = lines[0]
    text = panel.get_text("\n")
    maker = re.search(r"メーカー\s*[:：]\s*(.+)", text)
    if maker:
        fields["maker"] = maker.group(1).strip().split("\n")[0].strip()
    released = re.search(r"配信開始日\s*[:：]\s*(\d{4}-\d{2}-\d{2})", text)
    if released:
        fields["released"] = released.group(1)
    cast = re.search(r"出演者\s*[:：]\s*(.+)", text)
    if cast:
        names = [n.strip() for n in re.split(r"[/、,]", cast.group(1)) if n.strip()]
        if names:
            fields["cast"] = names
    return fields


def jav321_meta(number: str, use_cache: bool = True, allow_network: bool = True) -> Optional[dict]:
    """Official Japanese title from jav321.com — public HTML, used as the plot headline."""
    number = (number or "").strip()
    cid = _dmm_cid(number)
    if not cid:
        return None
    cache = {}
    if use_cache and JAV321_CACHE_FILE.exists():
        try:
            cache = json.loads(JAV321_CACHE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cache = {}
        if number in cache or cid in cache:
            return cache.get(number) or cache.get(cid)
    if not allow_network:
        return None
    html = _get(f"{JAV321}/video/{cid}")
    if not html:
        return None
    fields = _parse_jav321(html)
    if not fields:
        return None
    if use_cache:
        cache[number] = fields
        cache[cid] = fields
        JAV321_CACHE_FILE.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return fields


def apply_fields(meta: dict, fields: dict | None, rec: dict | None = None) -> dict:
    """Copy JavDB / jav321 fields onto a Stremio Meta Object."""
    meta = dict(meta)
    fields = fields or {}
    cast = [c for c in (fields.get("cast") or []) if c and c != "N/A"]
    forum_cast = str((rec or {}).get("出演女优") or "").strip()
    if forum_cast and not any(forum_cast in c or c in forum_cast for c in cast):
        cast = [forum_cast] + cast
    if cast:
        meta["cast"] = cast
    if fields.get("genres"):
        meta["genres"] = fields["genres"]
    if fields.get("runtime"):
        meta["runtime"] = fields["runtime"]
    if fields.get("released"):
        meta["released"] = fields["released"]
    if fields.get("director"):
        directors = fields["director"]
        meta["director"] = directors if isinstance(directors, list) else [directors]
    if fields.get("rating"):
        meta["imdbRating"] = str(fields["rating"])
    if fields.get("title") and fields["title"] not in (meta.get("name") or ""):
        meta["name"] = f"{(rec or {}).get('titleno') or ''} {fields['title']}".strip()
    if fields.get("poster") and not meta.get("poster"):
        meta["poster"] = fields["poster"]
    if fields.get("fanart") and not meta.get("background"):
        meta["background"] = fields["fanart"]
    composed = compose_description(rec, fields, meta.get("description") or "")
    if composed:
        meta["description"] = composed
    return meta


def enrich_meta(
    base_meta: dict,
    number: str,
    rec: dict | None = None,
    allow_network: bool = True,
    allow_jav321: bool = False,
) -> dict:
    """Merge JavDB (and jav321 title) into a base Stremio Meta Object."""
    meta = dict(base_meta)
    fields = javdb_meta(number, allow_network=allow_network) or {}
    if JAV_CODE_RE.search(number or "") and not fields.get("title") and not fields.get("summary"):
        extra = jav321_meta(number, allow_network=allow_jav321 or allow_network)
        if extra:
            for key, value in extra.items():
                if value and not fields.get(key):
                    fields[key] = value
    if not fields:
        if rec:
            meta["description"] = compose_description(rec, {}, meta.get("description") or "")
        return meta
    return apply_fields(meta, fields, rec)


if __name__ == "__main__":
    import sys
    for no in (sys.argv[1:] or ["ABF-385"]):
        print(no, "->", javdb_meta(no))

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
ROOT = Path(__file__).resolve().parent.parent
CACHE_FILE = Path(__file__).resolve().parent / ".javdb_cache.json"
DELAY = 0.4          # seconds between JavDB requests (be polite to the site)
RETRIES = 3          # retry attempts on transient request failures
RETRY_BACKOFF = 1.0  # base backoff seconds, doubled each retry

_SESSION: Optional[requests.Session] = None


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
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
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
    return fields


def javdb_meta(number: str, use_cache: bool = True) -> Optional[dict]:
    """Fetch and parse JavDB details for a title number. Returns a field dict, or None if unavailable."""
    number = (number or "").strip()
    if not number:
        return None

    cache = _load_cache() if use_cache else {}
    if use_cache and number in cache:
        return cache[number]

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
    if fields:
        if use_cache:
            cache[number] = fields
            _save_cache(cache)
        return fields
    return None


def enrich_meta(base_meta: dict, number: str) -> dict:
    """Merge JavDB fields into a base Stremio Meta Object. Falls back to base meta on failure."""
    meta = dict(base_meta)
    fields = javdb_meta(number)
    if not fields:
        return meta

    if fields.get("cast"):
        meta["cast"] = fields["cast"]
    if fields.get("genres"):
        meta["genres"] = fields["genres"]
    if fields.get("runtime"):
        meta["runtime"] = fields["runtime"]
    if fields.get("released"):
        meta["released"] = fields["released"]
    # fold JavDB context into the description so the detail page reads fully
    extras = []
    if fields.get("maker"):
        extras.append(f"片商: {fields['maker']}")
    if fields.get("distributor"):
        extras.append(f"發行: {fields['distributor']}")
    if fields.get("series"):
        extras.append(f"系列: {fields['series']}")
    if fields.get("rating"):
        extras.append(f"JavDB評分: {fields['rating']}")
    if extras:
        meta["description"] = (meta.get("description", "") + "\n" + "\n".join(extras)).strip()
    return meta


if __name__ == "__main__":
    import sys
    for no in (sys.argv[1:] or ["ABF-385"]):
        print(no, "->", javdb_meta(no))

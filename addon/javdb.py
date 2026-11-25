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
import os
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
DELAY = float(os.environ.get("JAVDB_DELAY", "2.0"))
RETRIES = 6
RETRY_BACKOFF = 1.0
BLOCK_WAIT = float(os.environ.get("JAVDB_BLOCK_WAIT", "15"))
MAX_BLOCK_WAIT = float(os.environ.get("JAVDB_BLOCK_MAX", "300"))
JAV_CODE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])([A-Z]{2,10})[-_](\d{2,6})(?![A-Za-z0-9])"
)

_SESSION: Optional[requests.Session] = None
_CACHE: Optional[dict] = None
_BLOCK_STREAK = 0


def reset_block_streak() -> None:
    global _BLOCK_STREAK
    _BLOCK_STREAK = 0


def block_wait_seconds(streak: int) -> float:
    """15, 30, 60, 120, 240, then cap. Never abort the enrich job."""
    if streak <= 0:
        return 0.0
    return min(MAX_BLOCK_WAIT, BLOCK_WAIT * (2 ** (streak - 1)))


def note_http_status(status: int) -> float:
    """Track 403/429. Return seconds to sleep. 200 resets the streak."""
    global _BLOCK_STREAK
    if status in (403, 429):
        _BLOCK_STREAK += 1
        return block_wait_seconds(_BLOCK_STREAK)
    _BLOCK_STREAK = 0
    return 0.0


def _apply_cookie_header(session: requests.Session, raw: str) -> None:
    for part in (raw or "").split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name, value = name.strip(), value.strip()
        if name:
            session.cookies.set(name, value, domain="javdb.com", path="/")


def _session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        _SESSION.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        _SESSION.headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        _SESSION.headers["Accept-Language"] = "zh-TW,zh,en;q=0.8"
        _SESSION.headers["Referer"] = f"{JAVDB}/"
        _SESSION.cookies.set("over18", "1", domain="javdb.com", path="/")
        _SESSION.cookies.set("locale", "en", domain="javdb.com", path="/")
        _apply_cookie_header(_SESSION, os.environ.get("JAVDB_COOKIE", ""))
    return _SESSION


def _get(url: str, timeout: int = 20) -> Optional[str]:
    last_err = None
    for attempt in range(RETRIES):
        try:
            r = _session().get(url, timeout=timeout, allow_redirects=True)
            if r.status_code == 200:
                note_http_status(200)
                time.sleep(DELAY)
                return r.text
            last_err = f"HTTP {r.status_code}"
            if r.status_code in (403, 429):
                wait = note_http_status(r.status_code)
                ra = r.headers.get("Retry-After")
                if ra and ra.isdigit():
                    wait = max(wait, float(ra))
                print(f"javdb: HTTP {r.status_code}, backoff {wait:.0f}s then retry {url}")
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
            links = [a.get_text(strip=True) for a in value.select("a")]
            if links:
                fields["cast"] = [n for n in links if n]
            else:
                # No per-name links: JavDB renders the cast as one space-joined
                # plain-text span. Split on whitespace so each actress becomes a
                # distinct name (a "・" inside a name is kept, not split on).
                raw = value.get_text(" ", strip=True)
                fields["cast"] = [n for n in re.split(r"\s+", raw) if n]
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
    """Plot / official title first, then JavDB facts. Forum blurbs count as the summary.

    JavDB sometimes serves the generic magnet-sharing boilerplate as the
    summary ("An magnet links sharing website for adult movies, ..."). When that
    boilerplate is detected it is dropped, and the forum synopsis (the actual
    video description) becomes the primary plot line instead.
    """
    rec = rec or {}
    fields = fields or {}
    parts: list[str] = []
    raw_summary = (fields.get("summary") or fields.get("title") or "").strip()
    # The JavDB boilerplate is the magnet-sharing blurb, not the video synopsis.
    if raw_summary and re.search(r"an magnet links sharing", raw_summary, re.I):
        summary = ""
    else:
        summary = raw_summary
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


def credit_names(*groups) -> list[str]:
    """Split forum / JavDB / existing credits and keep the first unique name."""
    names: list[str] = []
    seen: set[str] = set()
    for group in groups:
        if isinstance(group, str):
            # Names may be joined by 、 , or whitespace (JavDB renders multi-actress
            # casts as one space-joined string). Split on all of those so each
            # actress becomes a distinct credit.
            parts = re.split(r"[/、,，\s]+", group)
        elif isinstance(group, list):
            parts = group
        else:
            continue
        for part in parts:
            name = str(part or "").strip()
            if not name or name.upper() == "N/A":
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            names.append(name)
    return names


# JavDB is fetched with a locale=en session, so its 類別 (genres) come back in
# English (e.g. "Roleplay", "Big tits"). The live titles.sqlite stores the
# Chinese genre names (e.g. 角色扮演, 巨乳). This map translates the English
# names onto the Chinese names that already exist in the DB so the stored rows
# and the catalog filter chips stay consistent.
EN_GENRE_TO_ZH = {
    "Roleplay": "角色扮演",
    "Girl": "美少女",
    "Creampie": "中出",
    "Big tits": "巨乳",
    "Pantyhose": "連褲襪",
    "Black pantyhose": "連褲襪",
    "Fishnets": "絲襪",
    "Stockings": "絲襪",
    "Facial": "顏射",
    "Cum in mouth": "口交",
    "Blowjob": "口交",
    "Uniform": "制服",
    "School uniform": "制服",
    "Wife": "已婚婦女",
    "Affair": "出軌",
    "Intrusion": "白天出軌",
    "Gang Intrusion": "白天出軌",
    "Tit Wank": "乳交",
    "Chizyo": "乳交",
    "Mature woman": "熟女",
    "Squirting": "潮吹",
    "Kiss": "接吻",
    "Insult": "凌辱",
    "Beautiful butt": "大屁股",
    "Beautiful leg": "美腳",
    "Group Sex": "多P",
    "Gangbang": "多P",
    "BDSM": "SM",
    "Masochism guy": "SM",
    "Tune": "調教",
    "POV": "第一人稱攝影",
    "School": "學校作品",
    "Debut / Retires": "首次亮相",
    "Kinship": "亂倫",
    "Glasses": "眼鏡",
    "Swimsuit": "泳裝",
    "Spasms": "絕頂高潮",
    "Massage": "按摩",
    "Footjob": "足交",
    "tall": "高挑",
    "Hot spring": "溫泉",
    "Virginity": "處女",
    "Teacher": "女教師",
    "Anime character": "動畫人物",
    "Sportswear": "運動",
    "Video recording": "紀錄片",
    "Temptation": "騙色",
    "Love potion": "藥物",
    # Stored English genres found in the live DB (JavDB locale=en tags).
    # Translated to Traditional Chinese so the meta detail page shows Chinese
    # genres and the catalog filter chips (Chinese options) stay consistent.
    "Chinese Subtitle": "中文字幕",
    "Short hair": "短髮",
    "Sex Only": "床戲",
    "Kimono": "和服",
    "Toilet": "廁所",
    "Maid": "女僕",
    "Sex Worker": "性工作者",
    "Nurse": "護士",
    "Knee socks": "膝上襪",
    "Deep throat": "深喉",
    "Soapland": "香皂",
    "Couple": "情侶",
    "Sex beside husband": "夫の前で",
    "Bunny girl": "兔女郎",
    "Prison": "監獄",
    "Bathing place": "入浴",
    "Small tits": "小胸",
    "Idol": "偶像",
    "Library": "圖書館",
    "Outdoor": "戶外",
    "Chikan": "痴漢",
    "Store": "商店",
    "Crapulence": "過剰",
    "Piss": "撒尿",
    "Car": "汽車",
    "Bondage": "縛り",
    "Anal sex": "肛交",
    "Tram": "路面電車",
    "Hypnosis": "催眠",
    "Missed last train": "末班電車",
    "Hairless pussy": "無毛",
    "Avenge": "復仇",
    "Swimming pool": "泳池",
    "Kemonomimi": "獣耳",
    "Rainy Day": "雨天",
    "Gym Room": "健身房",
    "Cheongsam": "旗袍",
    "Female Anchor": "女主播",
    "10 times a day": "一日十回",
    "Solowork": "獨唱",
    "Thanksgiving": "感恩節",
    "Quickie": "快速性行為",
    "Childhood sweetheart": "青梅竹馬",
    "Flexible body": "柔軟",
    "Private Cam": "私人攝影機",
    "Leakage": "洩漏",
    "Doctor": "醫師",
    "Lesbian": "百合",
    "Housewife": "家庭主婦",
    "Team Manager": "團隊經理",
    "Suntan": "曬黑",
    "Widow": "寡婦",
    "Dainty": "苗條",
    "Breast Milk": "母乳",
    "Detective": "偵探",
    "Black": "黑皮膚",
    "Private teacher": "家庭教師",
    "Tattoo": "紋身",
    "Wedding dress": "婚紗",
    "For women": "女性專用",
    "Age difference": "年齡差",
    "Flight attendant": "空姐",
    "Variety Show": "綜藝節目",
    "Married": "已婚",
    "Torture": "拷問",
    "Time stop": "時間停止",
    "Ugly man": "醜男",
    "Festival": "節日",
    "Cuckold": "戴綠帽",
    "Big Butt": "大屁股",
    "Debut Production": "出道作",
    "Orgasm": "高潮",
    "Slender": "苗條",
    "Subordinates / Colleagues": "部下/同事",
    "Gal": "辣妹",
    "Prostitutes": "應酬女",
    "schoolgirls": "女學生",
    "Sister": "姐妹",
    "Cowgirl": "牛仔女郎",
    "Giant": "大物",
}


def translate_genres(genres: list[str] | str | None) -> list[str]:
    """Map JavDB English genre names onto the Chinese names used in the DB.
    Names without a mapping (e.g. OL, 4K, NTR, 多P, Roleplay) are kept as-is."""
    if not genres:
        return []
    if isinstance(genres, str):
        genres = [genres]
    return [EN_GENRE_TO_ZH.get(g, g) for g in genres]


def apply_fields(meta: dict, fields: dict | None, rec: dict | None = None) -> dict:
    """Copy JavDB / jav321 fields onto a Stremio Meta Object."""
    meta = dict(meta)
    fields = fields or {}
    cast = credit_names((rec or {}).get("出演女优"), meta.get("cast"), fields.get("cast"))
    if cast:
        meta["cast"] = cast
    if fields.get("genres"):
        meta["genres"] = translate_genres(fields["genres"])
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

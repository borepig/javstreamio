#!/usr/bin/env python3
"""HK Cat III AV magnets via AvGood 香港三级伦理 catalog.

The 2nd SearXNG pass found 0 new magnets and surfaced no
JS-heavy URLs, so this pass targets the AvGood dedicated
HK Category III catalog (avgood.com/c/745/, paginated) directly:

1. Plain-HTTP fetch of every page of the catalog; parse each item's
   title + AvGood id (link /c/<id>.html).
2. Match items against the 147 Wikipedia titles missing magnets
   (addon/hk_cat3_av.json), comparing the cleaned Chinese names.
3. For every match, fetch the AvGood detail page and extract magnet
   links (magnet:?xt=urn:btih:<hash>).
4. Score by the user's priorities (+3 hi-res, +3 Cantonese original,
   -3 dubbing, +1 name match); write the winner to the live SQLite as
   a `hongkong` row (id `hk:cat3-<idx>`) with the fixed column order.

Resume-safe: rows that already have a magnet are skipped.

Usage:
    python3 addon/hk_cat3_avgood_match.py [--dry-run]
"""

import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
META_JSON = ROOT / "addon" / "hk_cat3_av.json"
DB = ROOT / "debrid-app" / "data" / "titles.sqlite"

AVGGOOD_BASE = "https://avgood.com/c/745"

MAGNET_RE = re.compile(r'magnet:\?xt=urn:btih:([A-Fa-f0-9]{40})')
HASH_RE = re.compile(r'urn:btih:([A-Fa-f0-9]{40})')

# User priority markers (case-insensitive).
HI_RES_RE = re.compile(
    r"1080p|2160p|2160|4k|bluray|blu-ray|remux|x264|x265|hevc|avc|高清|hd\b", re.I)
CANTONESE_RE = re.compile(r"粤語|粤语|cantonese|原聲|原音|original\s*audio", re.I)
DUB_RE = re.compile(r"國語|国语|mandarin|eng\s*dub|english\s*dub|雙語|双语|dubbed|雙語版|國語版", re.I)

# Each catalog card: <a class="card" href="/c/<id>.html"> ... <h3 class="card-title"><b>TITLE</b></h3>
CARD_RE = re.compile(r'<a class="card" href="(?:https?://avgood\.com)?/c/(\d+)\.html">.*?</a>', re.S)
CARD_TITLE_RE = re.compile(r'<h3 class="card-title"><b>(.*?)</b></h3>', re.S)
TAG_CLEAN_RE = re.compile(r'\[[^\]]*\]|【[^】]*】')
PAGINATION_RE = re.compile(r'href="(?:https?://avgood\.com)?/c/745/(\d+)/?"')

# Minimal traditional→simplified map covering the characters that appear in the
# AvGood titles (which are traditional) so they can be compared against the
# simplified Chinese names in hk_cat3_av.json.
TRAD2SIMP = {
    "絲": "丝", "亂": "乱", "與": "与", "親": "亲", "從": "从", "傷": "伤",
    "偵": "侦", "傾": "倾", "黨": "党", "圓": "圆", "國": "国", "夢": "梦",
    "實": "实", "學": "学", "屬": "属", "師": "师", "廣": "广", "應": "应",
    "強": "强", "錄": "录", "憶": "忆", "總": "总", "戀": "恋", "惡": "恶",
    "戲": "戏", "無": "无", "時": "时", "陽": "阳", "陳": "陈", "難": "难",
    "雲": "云", "風": "风", "飛": "飞", "飯": "饭", "記": "记", "語": "语",
    "貴": "贵", "車": "车", "連": "连", "遲": "迟", "邊": "边", "鐵": "铁",
    "銀": "银", "鎖": "锁", "鏡": "镜", "閨": "闺", "隸": "隶", "軍": "军",
    "獸": "兽", "齊": "齐", "龍": "龙", "裏": "里", "紅": "红", "發": "发",
    "會": "会", "傳": "传", "體": "体", "後": "后", "業": "业", "歲": "岁",
}


def fetch(url: str, timeout: int = 20) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (javstreamio)"})
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def clean_name(raw: str) -> str:
    """Strip [tag] / 【tag】 markers, normalize whitespace, and convert
    traditional characters to simplified so AvGood (traditional) titles can
    be compared against the simplified Chinese names in hk_cat3_av.json."""
    s = TAG_CLEAN_RE.sub("", raw)
    s = re.sub(r'\s+', ' ', s).strip().lower()
    s = ''.join(TRAD2SIMP.get(c, c) for c in s)
    return s


def extract_magnets(html: str) -> list[tuple[str, str]]:
    magnets = []
    for m in MAGNET_RE.finditer(html):
        magnets.append((m.group(0), m.group(1)))
    if not magnets:
        for m in HASH_RE.finditer(html):
            h = m.group(1)
            magnets.append((f"magnet:?xt=urn:btih:{h}", h))
    return magnets


def score_candidate(name: str, t: dict) -> int:
    s = 0
    if HI_RES_RE.search(name):
        s += 3
    if CANTONESE_RE.search(name):
        s += 3
    if DUB_RE.search(name):
        s -= 3
    for n in [t.get("zh", ""), t.get("en", "")]:
        if n and n.lower() in name.lower():
            s += 1
            break
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-pages", type=int, default=0)
    args = ap.parse_args()

    meta = json.loads(META_JSON.read_text(encoding="utf-8"))
    titles = meta["av"]
    zh_by_idx = {i: t.get("zh", "") for i, t in enumerate(titles)}

    db = sqlite3.connect(str(DB))
    db.execute("PRAGMA journal_mode=WAL")
    have = {r[0] for r in db.execute(
        "SELECT id FROM titles WHERE category='hongkong' AND magnet LIKE 'magnet:%'")}

    # 1) Walk the catalog pages, collecting (avgood_id, cleaned_title).
    items: list[tuple[str, str]] = []
    item_titles: dict[str, str] = {}  # avgood_id -> cleaned item title

    def scan_page(html: str) -> None:
        for m in CARD_RE.finditer(html):
            aid = m.group(1)
            tm = CARD_TITLE_RE.search(m.group(0))
            if tm:
                raw_title = clean_name(tm.group(1))
                if aid and raw_title:
                    items.append((aid, raw_title))
                    item_titles[aid] = raw_title

    # Page 1 also reveals the pagination extent (max page number).
    html = fetch(AVGGOOD_BASE + "/")
    page_nums = [int(m.group(1)) for m in PAGINATION_RE.finditer(html)]
    max_page = min(max(page_nums) if page_nums else 1, args.max_pages or 1 << 30)
    scan_page(html)
    for page in range(2, max_page + 1):
        html = fetch(f"{AVGGOOD_BASE}/{page}/")
        scan_page(html)
        time.sleep(0.5)
    print(f"[avgood] catalog pages={max_page} items={len(items)}", flush=True)

    # 2) Match items against missing Wikipedia titles (by cleaned zh name).
    missing = [i for i in range(len(titles)) if f"hk:cat3-{i}" not in have]
    zh_lookup = {clean_name(zh_by_idx[i]): i for i in missing if zh_by_idx[i]}
    matched: dict[str, int] = {}  # avgood_id -> wiki idx
    for aid, title in items:
        if title in zh_lookup:
            matched[aid] = zh_lookup[title]
    print(f"[avgood] matched {len(matched)} of {len(missing)} missing titles", flush=True)

    # 3) For each match, fetch the detail page and extract magnets.
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    found = nomag = 0
    for aid, widx in matched.items():
        t = titles[widx]
        slug = f"hk:cat3-{widx}"
        try:
            html = fetch(f"https://avgood.com/c/{aid}.html")
        except Exception as e:
            print(f"[avgood] {slug} detail fetch failed: {e}", flush=True)
            continue
        mags = extract_magnets(html)
        item_title = item_titles.get(aid, "")
        best = None  # (score, magnet, hash)
        for magnet, h in mags:
            s = score_candidate(f"{item_title} {magnet}", t)
            if best is None or s > best[0]:
                best = (s, magnet, h)
        zh, en, year = t.get("zh", ""), t.get("en", ""), t.get("year", "")
        if best:
            found += 1
            name = en or zh
            desc_parts = [zh]
            if en:
                desc_parts.append(en)
            desc_parts.append(f"(Wikipedia 香港三级片列表 {year}; AvGood c/{aid})")
            released = f"{year}-01-01T00:00:00.000Z"
            search_text = " ".join(x for x in [zh, en, year] if x).lower()
            if not args.dry_run:
                db.execute(
                    """INSERT INTO titles (id, name, description, released,
                                         magnet, info_hash, posted_at, search_text, category, meta_rank)
                       VALUES (?,?,?,?,?,?,?, ?, 'hongkong', 0)
                       ON CONFLICT(id) DO UPDATE SET
                         description=excluded.description,
                         released=excluded.released,
                         magnet=excluded.magnet,
                         info_hash=excluded.info_hash,
                         posted_at=excluded.posted_at,
                         search_text=excluded.search_text,
                         meta_rank=0
                    """,
                    (slug, name, " ".join(desc_parts), released, best[1],
                     best[2], now, search_text),
                )
                db.commit()
            print(f"[avgood] {slug} {zh} ({year}) -> magnet score={best[0]} avgood c/{aid}", flush=True)
        else:
            nomag += 1
            print(f"[avgood] {slug} {zh} ({year}) -> no magnet on AvGood page", flush=True)
        time.sleep(0.5)

    db.close()
    print(f"done-avgood: matched={len(matched)} found={found} no_magnet={nomag}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

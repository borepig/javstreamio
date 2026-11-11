"""Generate the Stremio addon files (manifest, catalog, meta, streams) from the scraped JSONL data.

Output layout (served by GitHub raw URLs once committed):
  addon/manifest.json
  addon/catalog/movie/jav-censored.json
  addon/catalog/movie/jav-uncensored.json
  addon/catalog/movie/western.json
  addon/catalog/movie/others.json
  addon/meta/movie/jav:<titleno>.json
  addon/stream/movie/jav:<titleno>.json
  debrid-app/data/titles.json   (Vercel-bundled catalogs; JAV uses DMM, Western/Others keep sehuatang covers as forumPoster)
"""
from __future__ import annotations

import hashlib
import html as html_module
import json
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from javdb import apply_fields, cache_fits, credit_names, enrich_meta, javdb_meta, reset_block_streak
from enrich_hentai import parse_sukebei_name, slugify
from scrape import Site, forum_identity, looks_blocked, movie_record, parse_forum

ROOT = Path(__file__).resolve().parent.parent
ADDON_DIR = ROOT / "addon"
TITLES_JSON = ROOT / "debrid-app" / "data" / "titles.json"
TITLES_SQLITE = ROOT / "debrid-app" / "data" / "titles.sqlite"
CATALOG_ID = "jav103"  # legacy alias of jav-censored
ADDON_ID = "community.javstremio"
VERSION = "0.5.23"
CATALOG_LIMIT = 200
# Free tier (debrid-app/lib/titles-db.js) serves the newest FREE_CATALOG_SIZE
# titles per category; the JavDB free-set enrichment fetches exactly those.
FREE_CATALOG_SIZE = 15
HASH_RE = re.compile(r"urn:btih:([A-Za-z0-9]+)", re.I)
THREAD_ID_RE = re.compile(r"thread-(\d+)")
# Characters that break the per-title file name (Linux path separator) or the
# Stremio URL path (query/fragment delimiters). Replaced with "~" so the file
# name and the /meta/movie/<id> request stay consistent.
UNSAFE_ID_RE = re.compile(r"[/\\?#]")
# ext4 (Linux) limits a single filename component to 255 bytes.
MAX_FILENAME_BYTES = 255
SUFFIX_BYTES = 5  # "-<4-hex>" appended when the id had to be truncated


def _truncate_to_bytes(text: str, max_bytes: int) -> str:
    """Shorten `text` so its UTF-8 encoding fits in max_bytes, never splitting a character."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    cut = encoded[:max_bytes]
    while True:
        try:
            return cut.decode("utf-8")
        except UnicodeDecodeError:
            cut = cut[:-1]
RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
DATE_ONLY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})$")
FC2_RE = re.compile(r"(?i)FC2(?:[-_]?PPV)?[-_]?\d+")
JAV_CODE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])([A-Z]{2,10})[-_](\d{2,6})(?![A-Za-z0-9])"
)
WESTERN_DATE_RE = re.compile(
    r"(?i)(?:^|[\s\[【])[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)*\.\d{2}\.\d{2}\.\d{2}"
)
ENGLISH_DOT_NAME_RE = re.compile(r"(?i)^[A-Za-z]{2,}(?:\.[A-Za-z]{2,})+$")
PUBLIC_ART_HOSTS = ("pics.dmm.co.jp", "jdbstatic.com", "fourhoi.com", "image.mgstage.com", "avgood.com")
BLOCKED_ART_HOSTS = ("vercel.app", "tu.djhdhs.us", "w4kmzkm.com")
CATALOGS = (
    ("jav-censored", "JAV Censored"),
    ("jav-uncensored", "JAV Uncensored"),
    ("western", "Western"),
    ("others", "Others"),
    ("hentai-censored", "Hentai Censored"),
    ("hentai-uncensored", "Hentai Uncensored"),
    # korean, chinese, hongkong stay paused pending the user's source vetting.
)
# Hnt (Sukebei/Nyaa) catalogs are served as Stremio series (TV-show model),
# every other catalog stays a movie.
HNT_CATALOG_IDS = {"hentai-censored", "hentai-uncensored"}


def load_records() -> list[dict]:
    records: list[dict] = []
    seen: set[str] = set()
    for path in sorted((ROOT / "data").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = (rec.get("url") or rec.get("titleno") or "").strip()
            if not key or key in seen:
                continue
            if not (rec.get("magnet") or "").strip():
                continue
            seen.add(key)
            records.append(rec)
    return records


def info_hash(magnet_field: str) -> str:
    for line in (magnet_field or "").splitlines():
        m = HASH_RE.search(line)
        if m:
            return m.group(1).upper()
    return ""


def dmm_poster(titleno: str, size: str = "pl") -> str:
    """Public DMM cover URL. `ps` is the small catalog poster; `pl` is full fanart.

    These hosts do not Cloudflare-challenge image GETs, so Stremio can load
    them without storing or proxying images on Vercel.
    """
    raw = (titleno or "").strip()
    if raw.lower().startswith("thread") or raw.lower().startswith("fc2"):
        return ""
    match = re.match(r"^([A-Za-z]{2,})[-_]?0*(\d+)$", raw)
    if not match:
        return ""
    code = match.group(1).lower() + match.group(2).zfill(5)
    suffix = "ps" if size == "ps" else "pl"
    return f"https://pics.dmm.co.jp/digital/video/{code}/{code}{suffix}.jpg"


MONO1_PREFIX = re.compile(r"^(akdld|dldss|fns|mist|nhdtc|stars|start|sw)$", re.I)


def dmm_mono_poster(titleno: str, size: str = "ps") -> str:
    """Retail DMM mono jacket. Used when the digital CID is NOW PRINTING."""
    raw = (titleno or "").strip()
    match = re.match(r"^([A-Za-z]{2,})[-_](\d+)$", raw)
    if not match:
        return ""
    prefix = match.group(1).lower()
    if not MONO1_PREFIX.match(prefix):
        return ""
    cid = f"1{prefix}{int(match.group(2))}"
    suffix = "pl" if size == "pl" else "ps"
    return f"https://pics.dmm.co.jp/mono/movie/adult/{cid}/{cid}{suffix}.jpg"


def fourhoi_poster(titleno: str) -> str:
    """Public fourhoi jacket. Used when DMM only has a NOW PRINTING stub."""
    raw = (titleno or "").strip()
    if raw.lower().startswith("thread") or raw.lower().startswith("fc2"):
        return ""
    match = re.match(r"^([A-Za-z]{2,})[-_](\d+)$", raw)
    if not match:
        return ""
    return f"https://fourhoi.com/{match.group(1).lower()}-{match.group(2)}/cover-n.jpg"


PRESTIGE_PREFIX = re.compile(r"^(abf|abp|abs|aka|blo|chn|dic|dom|edd|mega|ppt|sga|tre|wpc|yrh)$", re.I)


def mgstage_poster(titleno: str) -> str:
    """Public MGStage Prestige jacket. Third source after DMM / fourhoi."""
    raw = (titleno or "").strip()
    match = re.match(r"^([A-Za-z]{2,})[-_](\d+)$", raw)
    if not match:
        return ""
    prefix = match.group(1).lower()
    if not PRESTIGE_PREFIX.match(prefix):
        return ""
    num = str(int(match.group(2)))
    return f"https://image.mgstage.com/images/prestige/{prefix}/{num}/pb_e_{prefix}-{match.group(2)}.jpg"


def is_public_art(url: str) -> bool:
    if not url or not str(url).startswith("https://"):
        return False
    try:
        host = url.split("/")[2].lower()
    except IndexError:
        return False
    if any(blocked in host for blocked in BLOCKED_ART_HOSTS):
        return False
    return any(host == allowed or host.endswith("." + allowed) for allowed in PUBLIC_ART_HOSTS)


def forum_cover(rec: dict) -> str:
    """First sehuatang preview image. Stremio cannot hotlink these; Vercel /poster can."""
    images = rec.get("preview_images") or []
    for img in images:
        if isinstance(img, str) and img.startswith("https://") and "djhdhs.us" in img:
            return img
    return ""


def public_art(rec: dict, javdb_fields: dict | None = None) -> tuple[str, str]:
    """Catalog poster (small) and detail fanart (full). Never forum or Vercel URLs."""
    no = rec.get("titleno") or ""
    poster = dmm_poster(no, "ps")
    background = dmm_poster(no, "pl")
    if javdb_fields:
        jp = javdb_fields.get("poster") or ""
        jf = javdb_fields.get("fanart") or jp
        if not poster and is_public_art(jp):
            poster = jp
        if not background and is_public_art(jf):
            background = jf
    return poster, background


HK_MARKERS = ("香港", "港片", "Cantonese", "粤语", "广东话", "廣東話", "粵語")


def classify_record(rec: dict) -> str:
    """Split scrape rows into jav-censored / jav-uncensored / western / others / hongkong.

    Order: HK markers (香港/港片/Cantonese/粤语) → hongkong; FC2PPV → others;
    JAV `XXXXX-NNN` uses 【是否有码】; English studio.date / actress names →
    western; everything else → others.
    """
    titleno = rec.get("titleno") or ""
    title = rec.get("title") or ""
    film = rec.get("影片名称") or ""
    tag = rec.get("tag") or ""
    blob = f"{titleno} {title} {film} {tag}"
    if any(marker in blob for marker in HK_MARKERS):
        return "hongkong"
    if FC2_RE.search(blob):
        return "others"
    if JAV_CODE_RE.search(titleno) or JAV_CODE_RE.search(blob):
        flag = f"{rec.get('是否有码') or ''} {rec.get('tag') or ''}"
        if "无码" in flag:
            return "jav-uncensored"
        return "jav-censored"
    actress = (rec.get("出演女优") or "").strip().lstrip(".")
    if WESTERN_DATE_RE.search(titleno) or WESTERN_DATE_RE.search(film):
        return "western"
    if actress and ENGLISH_DOT_NAME_RE.match(actress.replace(" ", ".")):
        return "western"
    return "others"


def record_description(rec: dict) -> str:
    return (
        f"出演女优: {rec.get('出演女优', '')}\n"
        f"容量: {rec.get('影片容量', '')}\n"
        f"有码/无码: {rec.get('是否有码', '')}"
    )


def record_cast(rec: dict) -> list[str]:
    """Map sehuatang 【出演女优】 into Stremio `cast`."""
    raw = (rec.get("出演女优") or "").strip().lstrip(".")
    if not raw or raw.upper() == "N/A":
        return []
    chunks = re.split(r"(?i)(?:\s+|\.)And(?:\s+|\.)|[/、,，]+", raw)
    names: list[str] = []
    for chunk in chunks:
        name = chunk.strip().lstrip(".")
        if not name:
            continue
        if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", name) and " " in name:
            names.extend(part for part in name.split() if part)
            continue
        dotted = name.replace(" ", ".")
        if ENGLISH_DOT_NAME_RE.match(dotted):
            name = name.replace(".", " ")
        names.append(name)
    return names


def record_name(rec: dict) -> str:
    return f"{rec.get('titleno', '')} {rec.get('title', '')}".strip()


def catalog_extra() -> list[dict]:
    return [
        {"name": "search", "isRequired": False},
        {"name": "skip", "isRequired": False},
    ]


def title_search_text(title: dict) -> str:
    parts = [title.get("id"), title.get("name")]
    parts.extend(title.get("cast") or [])
    parts.extend(title.get("genres") or [])
    joined = " ".join(str(part) for part in parts if part).lower()
    hyphenless = joined.replace("-", "")
    compact = re.sub(r"[^a-z0-9\u3040-\u30ff\u4e00-\u9fff]+", "", joined)
    return f"{joined} {hyphenless} {compact}".strip()


def load_bundled_overlay() -> dict[str, dict]:
    if not TITLES_JSON.is_file():
        return {}
    data = json.loads(TITLES_JSON.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for rows in data.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if row and row.get("id"):
                out[row["id"]] = row
    return out


def overlay_title(title: dict, bundled: dict[str, dict]) -> dict:
    old = bundled.get(title.get("id") or "")
    if not old:
        return title
    for key in ("name", "description", "cast", "genres", "director", "imdbRating", "runtime", "released", "forumPoster"):
        if old.get(key) not in (None, "", []):
            title[key] = old[key]
    if old.get("dmmMissing"):
        title["dmmMissing"] = True
    if old.get("fourhoiMissing"):
        title["fourhoiMissing"] = True
    return title


def title_sqlite_meta_rank(title: dict) -> int:
    """Precomputed catalog rank: 0 = enriched (has genres or description), 1 = bare."""
    has_genres = bool(title.get("genres"))
    has_desc = bool((title.get("description") or "").strip())
    return 0 if (has_genres or has_desc) else 1


def title_to_sqlite_row(title: dict) -> tuple:
    """Map one title dict onto the `titles` table column order."""
    return (
        title.get("id") or "",
        title.get("category") or "others",
        title.get("name") or "",
        title.get("description") or "",
        title.get("releaseInfo") or "",
        title.get("released") or "",
        title.get("runtime") or "",
        title.get("poster") or "",
        title.get("background") or "",
        title.get("forumPoster") or "",
        1 if title.get("dmmMissing") else 0,
        1 if title.get("fourhoiMissing") else 0,
        json.dumps(title.get("cast") or [], ensure_ascii=False),
        json.dumps(title.get("genres") or [], ensure_ascii=False),
        title.get("director") or "",
        title.get("imdbRating") or "",
        title.get("infoHash") or "",
        title.get("magnet") or "",
        title.get("posted_at") or title.get("released") or "",
        title_search_text(title),
        title_sqlite_meta_rank(title),
    )


def write_titles_sqlite(titles: list[dict], dest: Path | None = None) -> Path:
    dest = dest or TITLES_SQLITE
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".sqlite.tmp")
    if tmp.exists():
        tmp.unlink()
    conn = sqlite3.connect(tmp)
    try:
        conn.execute(
            """
            CREATE TABLE titles (
              id TEXT PRIMARY KEY,
              category TEXT NOT NULL,
              name TEXT NOT NULL,
              description TEXT DEFAULT '',
              release_info TEXT DEFAULT '',
              released TEXT DEFAULT '',
              runtime TEXT DEFAULT '',
              poster TEXT DEFAULT '',
              background TEXT DEFAULT '',
              forum_poster TEXT DEFAULT '',
              dmm_missing INTEGER DEFAULT 0,
              fourhoi_missing INTEGER DEFAULT 0,
              cast_json TEXT DEFAULT '[]',
              genres_json TEXT DEFAULT '[]',
              director TEXT DEFAULT '',
              imdb_rating TEXT DEFAULT '',
              info_hash TEXT DEFAULT '',
              magnet TEXT DEFAULT '',
              posted_at TEXT DEFAULT '',
              search_text TEXT DEFAULT '',
              meta_rank INTEGER DEFAULT 0
            )
            """
        )
        rows = [
            title_to_sqlite_row(title)
            for title in titles
            if title.get("id") and title.get("infoHash")
        ]
        conn.executemany(
            """
            INSERT OR REPLACE INTO titles (
              id, category, name, description, release_info, released, runtime,
              poster, background, forum_poster, dmm_missing, fourhoi_missing,
              cast_json, genres_json, director, imdb_rating, info_hash, magnet,
              posted_at, search_text, meta_rank
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        # Catalog ORDER BY (meta_rank, posted_at DESC, id DESC) is then served
        # from this index instead of a full-table scan (34k+ rows).
        conn.execute(
            "CREATE INDEX idx_titles_cat_rank ON titles (category, meta_rank, posted_at, id)"
        )
        conn.execute("CREATE INDEX idx_titles_cat_posted ON titles(category, posted_at DESC, id DESC)")
        conn.commit()
    finally:
        conn.close()
    tmp.replace(dest)
    return dest


def jav_code_from_id(meta_id: str) -> str:
    text = str(meta_id or "")
    return text[4:] if text.lower().startswith("jav:") else text


def _parse_json_list(raw) -> list:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def load_sqlite_titles(dest: Path | None = None) -> list[dict]:
    dest = dest or TITLES_SQLITE
    conn = sqlite3.connect(dest)
    conn.row_factory = sqlite3.Row
    try:
        rows = []
        for row in conn.execute("SELECT * FROM titles"):
            item = {
                "id": row["id"],
                "category": row["category"],
                "name": row["name"],
                "description": row["description"] or "",
                "releaseInfo": row["release_info"] or "",
                "released": row["released"] or "",
                "runtime": row["runtime"] or "",
                "poster": row["poster"] or "",
                "background": row["background"] or "",
                "forumPoster": row["forum_poster"] or "",
                "director": row["director"] or "",
                "imdbRating": row["imdb_rating"] or "",
                "infoHash": row["info_hash"] or "",
                "magnet": row["magnet"] or "",
                "posted_at": row["posted_at"] or "",
            }
            if row["dmm_missing"]:
                item["dmmMissing"] = True
            if row["fourhoi_missing"]:
                item["fourhoiMissing"] = True
            cast = _parse_json_list(row["cast_json"])
            genres = _parse_json_list(row["genres_json"])
            if cast:
                item["cast"] = cast
            if genres:
                item["genres"] = genres
            rows.append(item)
        return rows
    finally:
        conn.close()


def _director_text(value) -> str:
    if isinstance(value, list):
        return " / ".join(str(part) for part in value if part)
    return str(value or "")


def update_sqlite_title(dest: Path, title: dict) -> None:
    """Write JavDB fields onto one live row. posted_at / category stay put so
    the catalog remains newest-first."""
    released = rfc3339_released(title.get("released")) or ""
    release_info = title.get("releaseInfo") or (released[:4] if released else "")
    director = _director_text(title.get("director"))
    runtime = title.get("runtime") or ""
    rating = title.get("imdbRating") or ""
    conn = sqlite3.connect(dest)
    try:
        conn.execute(
            """
            UPDATE titles SET
              name = COALESCE(NULLIF(?, ''), name),
              description = COALESCE(NULLIF(?, ''), description),
              release_info = COALESCE(NULLIF(?, ''), release_info),
              released = COALESCE(NULLIF(?, ''), released),
              runtime = COALESCE(NULLIF(?, ''), runtime),
              cast_json = ?,
              genres_json = ?,
              director = COALESCE(NULLIF(?, ''), director),
              imdb_rating = COALESCE(NULLIF(?, ''), imdb_rating),
              search_text = ?
            WHERE id = ?
            """,
            (
                title.get("name") or "",
                title.get("description") or "",
                release_info,
                released,
                runtime,
                json.dumps(title.get("cast") or [], ensure_ascii=False),
                json.dumps(title.get("genres") or [], ensure_ascii=False),
                director,
                rating,
                title_search_text(title),
                title.get("id") or "",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def enrich_sqlite_rows(rows: list[dict], lookup, allow_network: bool = False) -> list[dict]:
    """Apply JavDB fields to JAV rows. Lookup order is newest first; output
    order is unchanged so the live catalog does not jump to the oldest title."""
    by_id = {row["id"]: dict(row) for row in rows if row.get("id")}
    work = [
        row
        for row in rows
        if row.get("id") and row.get("category") in ("jav-censored", "jav-uncensored")
    ]
    work.sort(key=lambda row: (row.get("posted_at") or "", row.get("id") or ""), reverse=True)
    for row in work:
        current = by_id[row["id"]]
        genres = current.get("genres") or []
        if genres:
            continue
        code = jav_code_from_id(row["id"])
        fields = lookup(code, False)
        if not cache_fits(code, fields):
            fields = None
        if allow_network and not genres and not (fields and fields.get("genres")):
            fetched = lookup(code, True)
            if cache_fits(code, fetched):
                fields = fetched
        if not fields:
            continue
        rec = {
            "titleno": code,
            "title": current.get("name") or "",
            "出演女优": " / ".join(current.get("cast") or []),
        }
        meta = {
            "id": current["id"],
            "type": "movie",
            "name": current.get("name") or "",
            "description": current.get("description") or "",
        }
        if current.get("cast"):
            meta["cast"] = list(current["cast"])
        if current.get("runtime"):
            meta["runtime"] = current["runtime"]
        if current.get("released"):
            meta["released"] = current["released"]
        enriched = apply_fields(meta, fields, rec)
        current = dict(current)
        for key in ("name", "description", "cast", "genres", "director", "imdbRating", "runtime", "released"):
            if enriched.get(key):
                current[key] = enriched[key]
        released = rfc3339_released(current.get("released"))
        if released:
            current["released"] = released
        by_id[row["id"]] = current
    return [by_id.get(row["id"], row) for row in rows]


def _title_meta_changed(old: dict, new: dict) -> bool:
    for key in ("genres", "cast", "imdbRating", "description", "runtime", "name"):
        if (new.get(key) or "") != (old.get(key) or ""):
            return True
    return False


def records_by_meta_id() -> dict[str, dict]:
    return {make_meta_id(rec): rec for rec in load_records()}


def merge_sqlite_casts(dest: Path, records: dict[str, dict] | None = None) -> int:
    """Put forum actress names back without changing catalog order or genres."""
    records = records if records is not None else records_by_meta_id()
    updated = 0
    for title in load_sqlite_titles(dest):
        if title.get("category") not in ("jav-censored", "jav-uncensored"):
            continue
        rec = records.get(title["id"]) or {}
        fields = javdb_meta(jav_code_from_id(title["id"]), allow_network=False) or {}
        merged = credit_names(rec.get("出演女优"), title.get("cast"), fields.get("cast"))
        if merged and merged != (title.get("cast") or []):
            title["cast"] = merged
            update_sqlite_title(dest, title)
            updated += 1
    return updated


def enrich_live_sqlite(
    dest: Path | None = None,
    *,
    allow_network: bool = True,
    limit: int = 0,
    free_set: bool = False,
) -> dict:
    """One-off / resume job: write cached JavDB into live SQLite, then fetch
    remaining JAV titles newest-first. Catalog sort stays posted_at DESC."""
    dest = dest or TITLES_SQLITE
    cast_restored = merge_sqlite_casts(dest)
    rows = load_sqlite_titles(dest)

    def cache_lookup(code: str, _network: bool):
        return javdb_meta(code, allow_network=False)

    cached = enrich_sqlite_rows(rows, cache_lookup, allow_network=False)
    applied = 0
    before = {row["id"]: row for row in rows}
    for title in cached:
        old = before.get(title["id"]) or {}
        if _title_meta_changed(old, title):
            update_sqlite_title(dest, title)
            applied += 1

    fetched = 0
    filled = 0
    if allow_network:
        missing = [
            row
            for row in cached
            if row.get("category") in ("jav-censored", "jav-uncensored")
            and not (row.get("genres") or [])
        ]
        missing.sort(key=lambda row: (row.get("posted_at") or "", row.get("id") or ""), reverse=True)
        if free_set:
            # Free tier only serves the newest FREE_CATALOG_SIZE titles per
            # category (titles-db.js isInFreeSet), so only fetch JavDB meta
            # for exactly that set; the paid tier keeps fetching everything.
            by_cat: dict[str, list[dict]] = {}
            for row in missing:
                by_cat.setdefault(row.get("category") or "", []).append(row)
            missing = []
            for rows_ in by_cat.values():
                missing.extend(rows_[:FREE_CATALOG_SIZE])
        reset_block_streak()
        for row in missing:
            if limit and fetched >= limit:
                break
            code = jav_code_from_id(row["id"])
            fields = javdb_meta(code, allow_network=True)
            fetched += 1
            if not cache_fits(code, fields):
                continue
            updated = enrich_sqlite_rows(
                [row],
                lambda c, _n, f=fields, want=code: f if c == want else None,
                allow_network=False,
            )[0]
            update_sqlite_title(dest, updated)
            filled += 1
            if filled % 25 == 0:
                print(f"sqlite enrich: fetched={fetched} filled={filled} last={row['id']}")
    return {
        "cache_applied": applied,
        "cast_restored": cast_restored,
        "fetched": fetched,
        "filled": filled,
        "total": len(rows),
    }


def scrape_new_to_sqlite(
    use_javdb: bool = False,
    max_pages: int = 0,
    batch: int = 6,
) -> dict:
    """Incremental sehuatang refresh.

    Scrape sehuatang forum 103 newest-first (release-date order), append only
    titles that are missing from the live SQLite, and stop as soon as a page
    yields no new titles (caught up with the current database). New records are
    also appended to data/forum-103-all.jsonl so full rebuilds stay complete.
    `use_javdb=False` keeps JavDB cache-only (no network) to avoid 403/429.
    """
    dest = TITLES_SQLITE
    jsonl = ROOT / "data" / "forum-103-all.jsonl"
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    bundled = load_bundled_overlay()

    conn = sqlite3.connect(dest)
    existing = {row[0] for row in conn.execute("SELECT id FROM titles")}

    base = "https://sehuatang.org/"
    site = Site()
    added = 0
    pages_done = 0
    caught_up_page = 0
    first_url = base + "forum-103-1.html"
    try:
        first_html = site.open(first_url)
        fid, _current, last = forum_identity(first_url, first_html)
        end_page = last if max_pages <= 0 else min(last, max_pages)
        for page_no in range(1, end_page + 1):
            page_url = f"{base}forum-{fid}-{page_no}.html"
            listing_html = first_html if page_no == 1 else str(site.fetch_many([page_url])[0]["html"])
            if looks_blocked(listing_html):
                print(f"page {page_no}: blocked; stopping", file=sys.stderr)
                break
            listing = parse_forum(listing_html, page_url)
            threads = listing["threads"]
            payloads = site.fetch_many([t["url"] for t in threads]) if threads else []
            by_url = {str(p["url"]): p for p in payloads}
            new_in_page = 0
            for thread in threads:
                payload = by_url.get(thread["url"])
                if not payload:
                    continue
                html = str(payload["html"])
                if int(payload["status"]) != 200 or looks_blocked(html):
                    continue
                rec = movie_record(html, thread)
                if not rec or not (rec.get("magnet") or "").strip():
                    continue
                meta_id = make_meta_id(rec)
                if meta_id in existing:
                    continue
                title = overlay_title(build_title(rec, use_javdb=use_javdb), bundled)
                title["posted_at"] = rec.get("posted_at") or title.get("released") or ""
                with jsonl.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(rec, ensure_ascii=False) + "\n")
                conn.execute(
                    """
                    INSERT OR REPLACE INTO titles (
                      id, category, name, description, release_info, released, runtime,
                      poster, background, forum_poster, dmm_missing, fourhoi_missing,
                      cast_json, genres_json, director, imdb_rating, info_hash, magnet,
                      posted_at, search_text, meta_rank
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    title_to_sqlite_row(title),
                )
                conn.commit()
                existing.add(meta_id)
                added += 1
                new_in_page += 1
            pages_done = page_no
            if new_in_page == 0:
                caught_up_page = page_no
                print(f"caught up at page {page_no}: no new titles; stopping")
                break
        conn.close()
    finally:
        site.close()
    return {
        "added": added,
        "pages": pages_done,
        "caught_up_page": caught_up_page,
        "total_in_db": len(existing),
    }


# --- AvGood HK AV scrape -----------------------------------------------------
# AvGood (avgood.com) hosts magnet/torrent indexes for Hong Kong AV in two
# sections: c/612 (香港三级) and c/595 (香港四级). Listing cards live on
# /c/<section>/<page>/; each card links to /c/<id>.html, whose detail page
# carries the magnet link, seed name and release date.
AVGOOD_BASE = "https://avgood.com"
AVGOOD_HK_SECTIONS = (("c/612", "香港三级"), ("c/595", "香港四级"))
AVGOOD_QUALITY_MARKERS = ("720p", "1080p", "4k", "hd", "bluray", "高清")
AVGOOD_AUDIO_MARKERS = ("cantonese", "粤语", "粤語", "mandarin", "国語", "国语")
AVGOOD_MAGNET_RE = re.compile(r'href="(magnet:\?xt=urn:btih:[^"]+)"')
AVGOOD_TORRENT_RE = re.compile(r"【种子名称】:(.+?)\.torrent")
AVGOOD_DATE_RE = re.compile(r"发行日期:(\d{4}-\d{2}-\d{2})")


def _avgood_parse_listing(html: str) -> list[dict]:
    """Cards on /c/<section>/<page>/: id, title, poster, category tag."""
    items: list[dict] = []
    for block in html.split('<div class="grid-item">')[1:]:
        id_m = re.search(r'<a class="card" href="/c/(\d+)\.html"', block)
        title_m = re.search(r'<h3 class="card-title"><b>(.*?)</b></h3>', block, re.S)
        poster_m = re.search(r'data-original="(/remote/[^"]+)"', block)
        tag_m = re.search(r'<span class="tag tag-category"[^>]*>(.*?)</span>', block, re.S)
        if not id_m or not title_m:
            continue
        items.append(
            {
                "id": id_m.group(1),
                "title": title_m.group(1).strip(),
                "poster": ("https://avgood.com" + poster_m.group(1)) if poster_m else "",
                "tag": tag_m.group(1).strip() if tag_m else "",
            }
        )
    return items


def _avgood_parse_detail(html: str) -> dict:
    magnet_m = AVGOOD_MAGNET_RE.search(html)
    torrent_m = AVGOOD_TORRENT_RE.search(html)
    date_m = AVGOOD_DATE_RE.search(html)
    return {
        "magnet": magnet_m.group(1) if magnet_m else "",
        "torrentName": torrent_m.group(1) if torrent_m else "",
        "released": date_m.group(1) if date_m else "",
    }


def _avgood_total_pages(html: str) -> int:
    m = re.search(r'class="page-total">(\d+)<', html)
    return int(m.group(1)) if m else 1


def scrape_avgood_hk(max_pages: int = 20, batch: int = 8, delay: float = 1.0) -> dict:
    """Scrape AvGood's HK sections and append HK AV titles to the live SQLite.

    Only high-quality entries are kept: the seed name or title must declare an
    HD quality marker (720p/1080p/4K/bluray/高清) or a Cantonese/Mandarin audio
    marker. Rows are stored as category 'hongkong' with id 'hk:avgood-<id>'
    so Stremio routes them to this addon via the 'hk:' idPrefixes.
    Resume-safe: ids already in the DB are skipped.
    """
    dest = TITLES_SQLITE
    conn = sqlite3.connect(dest)
    existing = {row[0] for row in conn.execute("SELECT id FROM titles WHERE id LIKE 'hk:%'")}
    site = Site()
    added = skipped = filtered = blocked = 0
    try:
        for section, _label in AVGOOD_HK_SECTIONS:
            # Land on the section page first: fetch_many() runs in-page
            # fetch(), which only works same-origin — site.open() waits for
            # sehuatang selectors, so navigate directly instead.
            site.page.goto(f"{AVGOOD_BASE}/{section}/", wait_until="domcontentloaded", timeout=45000)
            first_html = site.page.content()
            if looks_blocked(first_html):
                # Cloudflare "Just a moment" challenge: wait and reload once.
                site.page.wait_for_timeout(8000)
                site.page.goto(f"{AVGOOD_BASE}/{section}/", wait_until="domcontentloaded", timeout=45000)
                first_html = site.page.content()
                if looks_blocked(first_html):
                    blocked += 1
                    continue
            total = _avgood_total_pages(first_html)
            end_page = total if max_pages <= 0 else min(total, max_pages)
            for page_no in range(1, end_page + 1):
                html = first_html if page_no == 1 else str(site.fetch_many([f"{AVGOOD_BASE}/{section}/{page_no}/"])[0]["html"])
                if looks_blocked(html):
                    blocked += 1
                    continue
                items = _avgood_parse_listing(html)
                pending = [it for it in items if f"hk:avgood-{it['id']}" not in existing]
                for start in range(0, len(pending), batch):
                    chunk = pending[start : start + batch]
                    payloads = site.fetch_many([f"{AVGOOD_BASE}/c/{it['id']}.html" for it in chunk])
                    by_url = {str(p["url"]): p for p in payloads}
                    for it in chunk:
                        p = by_url.get(f"{AVGOOD_BASE}/c/{it['id']}.html")
                        if not p or int(p["status"]) != 200 or looks_blocked(str(p["html"])):
                            blocked += 1
                            continue
                        detail = _avgood_parse_detail(str(p["html"]))
                        if not detail["magnet"]:
                            continue
                        # All HK titles are kept (including lower-definition),
                        # so the full AvGood inventory lands in the metadata library.
                        title_id = f"hk:avgood-{it['id']}"
                        if title_id in existing:
                            skipped += 1
                            continue
                        title = {
                            "id": title_id,
                            "category": "hongkong",
                            "name": it["title"],
                            "description": (
                                f"{it['title']}\n\nSource: AvGood ({it['tag'] or 'HK'})"
                                + (f" | {detail['torrentName']}" if detail["torrentName"] else "")
                            ),
                            "releaseInfo": detail["released"][:4] if detail["released"] else "",
                            "released": detail["released"],
                            "poster": it["poster"],
                            "infoHash": info_hash(detail["magnet"]),
                            "magnet": detail["magnet"],
                            "posted_at": detail["released"],
                        }
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO titles (
                              id, category, name, description, release_info, released, runtime,
                              poster, background, forum_poster, dmm_missing, fourhoi_missing,
                              cast_json, genres_json, director, imdb_rating, info_hash, magnet,
                              posted_at, search_text, meta_rank
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            title_to_sqlite_row(title),
                        )
                        conn.commit()
                        existing.add(title_id)
                        added += 1
                    if delay:
                        time.sleep(delay)
        hk_total = conn.execute("SELECT COUNT(*) FROM titles WHERE category='hongkong'").fetchone()[0]
        conn.close()
    finally:
        site.close()
    return {
        "added": added,
        "skipped": skipped,
        "filtered": filtered,
        "blocked": blocked,
        "hk_in_db": hk_total,
    }


# --- iJavTorrent korean/chinese AV scrape ----------------------------------
# iJavTorrent (ijavtorrent.com) is the best public magnet source for Korean
# AV (tag /tag/korean-305) and Chinese AV (tag /tag/chinese-1543, 138 pages).
# Each listing card carries the movie id, name, date, cover
# (images.ijavtorrent.com) and magnet inline. Rows are stored as category
# 'korean'/'chinese' with id 'kr:ijav-<movie-id>' / 'cn:ijav-<movie-id>'
# so Stremio routes them via the 'kr:' / 'cn:' idPrefixes.

IJA_BASE = "https://ijavtorrent.com"
IJA_TAGS = {"korean": "korean-305", "chinese": "chinese-1543"}
IJA_ID_PREFIX = {"korean": "kr", "chinese": "cn"}
IJA_CATEGORY_LABEL = {"korean": "Korean AV", "chinese": "Chinese AV"}
IJA_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _ija_http_get(url: str) -> str:
    """GET with a few retries on transient 404/5xx (the CDN occasionally 404s mid-deploy)."""
    for attempt in range(3):
        req = urllib.request.Request(url, headers={"User-Agent": IJA_UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (404, 500, 502, 503) and attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            raise


def _ija_parse_listing(page_html: str) -> list[dict]:
    """Parse an iJavTorrent tag listing page into (id, name, date, poster, magnet, runtime).

    Each movie card carries `data-movie-id` twice: once on the card header
    (cover, name, date) and once on the torrent section (magnet link). Merge
    both blocks per movie id.
    """
    items: dict[str, dict] = {}
    order: list[str] = []
    for block in page_html.split('data-movie-id="')[1:]:
        id_m = re.match(r"(\d+)", block)
        if not id_m:
            continue
        mid = id_m.group(1)
        item = items.get(mid)
        if item is None:
            item = {"id": mid, "name": "", "released": "", "poster": "", "magnet": "", "runtime": ""}
            items[mid] = item
            order.append(mid)
        name_m = re.search(r'<div class="name">\s*<a[^>]*>\s*<span>(.*?)</span>', block, re.S)
        if name_m and not item["name"]:
            item["name"] = html_module.unescape(name_m.group(1)).strip()
        if not item["name"]:
            # Some cards omit the name span; the cover img's alt attribute
            # carries the full movie title.
            alt_m = re.search(
                r'<img[^>]*data-src="https://images\.ijavtorrent\.com/data/covers/[^"?]+"[^>]*alt="([^"]+)"',
                block,
                re.S,
            )
            if alt_m:
                item["name"] = html_module.unescape(alt_m.group(1)).strip()
        date_m = re.search(r"(\d{2}/\d{2}/\d{4})", block)
        if date_m and not item["released"]:
            d, m, y = date_m.group(1).split("/")
            item["released"] = f"{y}-{m}-{d}T00:00:00.000Z"
        poster_m = re.search(r'data-src="(https://images\.ijavtorrent\.com/data/covers/[^"?]+)', block)
        if poster_m and not item["poster"]:
            item["poster"] = poster_m.group(1)
        magnet_m = re.search(r'href="(magnet:\?xt=urn:btih:[^"]+)"', block)
        if magnet_m and not item["magnet"]:
            item["magnet"] = html_module.unescape(magnet_m.group(1))
        runtime_m = re.search(r"(?i)时长[:：]?\s*(\d+)\s*min", block)
        if runtime_m and not item["runtime"]:
            item["runtime"] = f"{runtime_m.group(1)} min"
    return [items[mid] for mid in order]


def scrape_ijavtorrent(category: str, max_pages: int = 10) -> dict:
    """Append iJavTorrent korean/chinese titles to the live SQLite.

    `category` must be 'korean' or 'chinese'. The listing is newest-first,
    so the run stops as soon as a page yields no new titles (caught up), and
    ids already in the DB are skipped — re-runs are resume-safe.
    """
    if category not in IJA_TAGS:
        raise ValueError(f"unknown iJavTorrent category: {category}")
    tag = IJA_TAGS[category]
    prefix = IJA_ID_PREFIX[category]
    label = IJA_CATEGORY_LABEL[category]
    conn = sqlite3.connect(TITLES_SQLITE)
    existing = {row[0] for row in conn.execute(
        "SELECT id FROM titles WHERE category = ?", (category,))}
    added = skipped = no_magnet = 0
    page = 1
    pages_done = 0
    caught_up_page = 0
    consecutive_failures = 0
    while page <= (max_pages or 99999):
        url = f"{IJA_BASE}/tag/{tag}" + (f"?page={page}" if page > 1 else "")
        try:
            html = _ija_http_get(url)
            consecutive_failures = 0
        except Exception as e:
            consecutive_failures += 1
            print(f"page {page}: fetch failed ({e}); skipping", file=sys.stderr)
            if consecutive_failures >= 2:
                print(f"2 consecutive fetch failures; stopping", file=sys.stderr)
                break
            page += 1
            continue
        items = _ija_parse_listing(html)
        new_items = [it for it in items if f"{prefix}:ijav-{it['id']}" not in existing]
        for it in items:
            title_id = f"{prefix}:ijav-{it['id']}"
            if title_id in existing:
                skipped += 1
                continue
            if not it["magnet"]:
                no_magnet += 1
                continue
            title = {
                "id": title_id,
                "category": category,
                "name": it["name"],
                "description": f"{it['name']}\n\nSource: iJavTorrent ({label})",
                "releaseInfo": it["released"][:4] if it["released"] else "",
                "released": it["released"],
                "runtime": it["runtime"],
                "poster": it["poster"],
                "infoHash": info_hash(it["magnet"]),
                "magnet": it["magnet"],
                "posted_at": it["released"],
                "genres": [label],
            }
            conn.execute(
                """
                INSERT OR REPLACE INTO titles (
                  id, category, name, description, release_info, released, runtime,
                  poster, background, forum_poster, dmm_missing, fourhoi_missing,
                  cast_json, genres_json, director, imdb_rating, info_hash, magnet,
                  posted_at, search_text, meta_rank
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                title_to_sqlite_row(title),
            )
            conn.commit()
            existing.add(title_id)
            added += 1
        pages_done = page
        page += 1
        if not new_items:
            caught_up_page = pages_done
            print(f"caught up at page {pages_done}: no new titles; stopping")
            break
        time.sleep(1.0)
    total = conn.execute(
        "SELECT COUNT(*) FROM titles WHERE category = ?", (category,)
    ).fetchone()[0]
    conn.close()
    return {
        "added": added,
        "skipped": skipped,
        "no_magnet": no_magnet,
        "pages": pages_done,
        "caught_up_page": caught_up_page,
        "total_in_db": total,
    }


# --- Nyaa/Sukebei hentai censored/uncensored scrape -----------------------
# Sukebei (sukebei.nyaa.si) is the canonical public torrent source for
# hentai. The anime-video search (?f=0&c=0_0&q=hentai / q=uncen) returns
# plain-HTML listing pages whose rows carry the torrent id (/view/<id>),
# title, size, date, and magnet. Rows are stored as category
# 'hentai-censored' / 'hentai-uncensored' with id 'hnt:nyaa-<torrent-id>'.

NYAA_BASE = "https://sukebei.nyaa.si"
NYAA_HENTAI_QUERIES = (("hentai-censored", "hentai"), ("hentai-uncensored", "uncen"))
NYAA_HENTAI_GENRES = {
    "hentai-censored": ["Hentai", "Censored"],
    "hentai-uncensored": ["Hentai", "Uncensored"],
}
NYAA_UA = IJA_UA


def _nyaa_http_get(url: str) -> str:
    for attempt in range(3):
        req = urllib.request.Request(url, headers={"User-Agent": NYAA_UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (404, 500, 502, 503) and attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            raise


def _nyaa_parse_listing(page_html: str) -> list[dict]:
    """Parse a Sukebei search-results page: (torrent id, title, size, date, magnet)."""
    items: list[dict] = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page_html, re.S):
        view_m = re.search(r'href="/view/(\d+)"', row)
        if not view_m:
            continue
        id = view_m.group(1)
        title_m = re.search(r'<td colspan="2">\s*<a[^>]*>(.*?)</a>', row, re.S)
        title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip() if title_m else ""
        size_m = re.search(r"([\d.]+)\s*(GiB|MiB|KiB)", row)
        date_m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", row)
        magnet_m = re.search(r'href="(magnet:\?xt=urn:btih:[^"]+)"', row)
        released = ""
        if date_m:
            date, clock = date_m.group(1).split(" ")
            released = f"{date}T{clock}:00.000Z"
        magnet = html_module.unescape(magnet_m.group(1)) if magnet_m else ""
        items.append(
            {
                "id": id,
                "name": title,
                "size": f"{size_m.group(1)} {size_m.group(2)}" if size_m else "",
                "released": released,
                "magnet": magnet,
            }
        )
    return items


def scrape_nyaa_hentai(max_pages: int = 5) -> dict:
    """Append Nyaa/Sukebei hentai titles to the live SQLite.

    Two runs: 'hentai-censored' (q=hentai) and 'hentai-uncensored'
    (q=uncen), both limited to the anime-video category (f=0&c=0_0).
    Resume-safe: existing 'hnt:nyaa-<id>' rows are skipped, and each run
    stops after a page yields no new titles.
    """
    conn = sqlite3.connect(TITLES_SQLITE)
    existing = {row[0] for row in conn.execute(
        "SELECT id FROM titles WHERE id LIKE 'hnt:nyaa-%'")}
    stats = {"hentai-censored": {"added": 0, "skipped": 0, "no_magnet": 0, "pages": 0},
              "hentai-uncensored": {"added": 0, "skipped": 0, "no_magnet": 0, "pages": 0}}
    for category, query in NYAA_HENTAI_QUERIES:
        page = 1
        consecutive_failures = 0
        while page <= max_pages:
            url = f"{NYAA_BASE}/?f=0&c=0_0&q={query}" + (f"&page={page}" if page > 1 else "")
            try:
                html = _nyaa_http_get(url)
                consecutive_failures = 0
            except Exception as e:
                consecutive_failures += 1
                print(f"{category} page {page}: fetch failed ({e})", file=sys.stderr)
                if consecutive_failures >= 2:
                    print(f"{category}: 2 consecutive fetch failures; stopping", file=sys.stderr)
                    break
                page += 1
                continue
            items = _nyaa_parse_listing(html)
            new_items = [it for it in items if f"hnt:nyaa-{it['id']}" not in existing]
            for it in items:
                title_id = f"hnt:nyaa-{it['id']}"
                if title_id in existing:
                    stats[category]["skipped"] += 1
                    continue
                if not it["magnet"]:
                    stats[category]["no_magnet"] += 1
                    continue
                title = {
                    "id": title_id,
                    "category": category,
                    "name": it["name"],
                    "description": (
                        f"{it['name']}\n\nSource: Nyaa/Sukebei"
                        + (f" ({it['size']})" if it["size"] else "")
                        + f" ({category})"
                    ),
                    "releaseInfo": it["released"][:4] if it["released"] else "",
                    "released": it["released"],
                    "infoHash": info_hash(it["magnet"]),
                    "magnet": it["magnet"],
                    "posted_at": it["released"],
                    "genres": NYAA_HENTAI_GENRES[category],
                }
                conn.execute(
                    """
                    INSERT OR REPLACE INTO titles (
                      id, category, name, description, release_info, released, runtime,
                      poster, background, forum_poster, dmm_missing, fourhoi_missing,
                      cast_json, genres_json, director, imdb_rating, info_hash, magnet,
                      posted_at, search_text, meta_rank
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    title_to_sqlite_row(title),
                )
                conn.commit()
                existing.add(title_id)
                stats[category]["added"] += 1
            stats[category]["pages"] = page
            page += 1
            if not new_items:
                print(f"{category}: caught up at page {page - 1}; stopping")
                break
            time.sleep(1.0)
    for category, _q in NYAA_HENTAI_QUERIES:
        total = conn.execute(
            "SELECT COUNT(*) FROM titles WHERE category = ?", (category,)
        ).fetchone()[0]
        stats[category]["total_in_db"] = total
    conn.close()
    return stats


def build_sqlite_titles(use_javdb: bool = False) -> list[dict]:
    bundled = load_bundled_overlay()
    titles = []
    for rec in load_records():
        if not info_hash(rec.get("magnet") or ""):
            continue
        title = overlay_title(build_title(rec, use_javdb=use_javdb), bundled)
        title["posted_at"] = rec.get("posted_at") or title.get("released") or ""
        titles.append(title)
    return titles


def _meta_rank(item: dict) -> int:
    # Rows with metadata (non-empty genres or description) rank 0 (front);
    # bare rows rank 1 (back). Mirrors META_RANK in debrid-app/lib/titles-db.js.
    genres = item.get("genres") or []
    description = (item.get("description") or "").strip()
    return 0 if (genres or description) else 1


def newest_by_category(records: list[dict], limit: int = CATALOG_LIMIT) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {key: [] for key, _ in CATALOGS}
    for rec in records:
        grouped.setdefault(classify_record(rec), []).append(rec)
    for key, rows in grouped.items():
        # Within each rank, newest first; meta-rich rows come before bare rows.
        rows.sort(key=lambda item: item.get("posted_at") or "", reverse=True)
        rows.sort(key=_meta_rank)  # stable: keeps the newest-first order inside each rank
        grouped[key] = rows[:limit]
    return {key: grouped.get(key, []) for key, _ in CATALOGS}


def make_meta_id(rec: dict) -> str:
    """Filename-safe id: use titleno if present, else the thread id from the URL.

    The titleno is sanitized so it is safe both as a file name (no "/" path
    separators) and in Stremio's /meta/movie/<id> request (no "?" / "#" URL
    delimiters). The sanitized id is used consistently for the catalog entry,
    the meta file name, and the stream file name. Long thread-title titlenos
    are truncated (by bytes, not characters) with a 4-hex hash suffix so the
    file name stays within the 255-byte limit and ids cannot collide.
    """
    no = (rec.get("titleno") or "").strip()
    if no:
        no = UNSAFE_ID_RE.sub("~", no)
        full = "jav:" + no
        if len((full + ".json").encode("utf-8")) > MAX_FILENAME_BYTES:
            digest = hashlib.sha1(full.encode("utf-8")).hexdigest()[:4]
            base_max = MAX_FILENAME_BYTES - len("jav:") - len(".json") - SUFFIX_BYTES
            no = _truncate_to_bytes(no, base_max) + "-" + digest
        return "jav:" + no
    m = THREAD_ID_RE.search(rec.get("url") or "")
    if m:
        return "jav:thread-" + m.group(1)
    return "jav:unknown"


def rfc3339_released(value) -> str | None:
    """stremio-core parses `released` as RFC3339 DateTime. A date-only
    string like `2026-05-06` fails that parse and drops the whole Meta."""
    if not value:
        return None
    text = str(value).strip()
    if RFC3339_RE.match(text):
        return text
    match = DATE_ONLY_RE.match(text)
    if match:
        return match.group(1) + "T00:00:00.000Z"
    return None


def sanitize_meta_item(meta: dict) -> dict:
    meta = dict(meta)
    released = rfc3339_released(meta.get("released"))
    if released:
        meta["released"] = released
    else:
        meta.pop("released", None)
    meta_id = meta.get("id") or ""
    hints = dict(meta.get("behaviorHints") or {})
    if meta_id and not hints.get("defaultVideoId"):
        hints["defaultVideoId"] = meta_id
    if hints.get("hasScheduledVideos") is None:
        hints["hasScheduledVideos"] = False
    meta["behaviorHints"] = hints
    return meta


def as_meta_resource(body: dict) -> dict:
    """Return a Stremio ResourceResponse.Meta payload.

    stremio-core deserializes /meta as `{ "meta": <Meta Object> }`. A bare
    Meta Object or `{ "meta": [ ... ] }` both fail to parse, which is the
    client-side "No metadata was found!" screen.
    """
    if not isinstance(body, dict):
        return body
    inner = body.get("meta")
    if isinstance(inner, dict) and inner.get("id"):
        return {"meta": sanitize_meta_item(inner)}
    if isinstance(inner, list) and inner and isinstance(inner[0], dict):
        return {"meta": sanitize_meta_item(inner[0])}
    if body.get("id") and body.get("type"):
        return {"meta": sanitize_meta_item(body)}
    return body


def uses_online_meta(rec: dict) -> bool:
    """JAV titles get JavDB/jav321. Western and Others stay on sehuatang.org fields."""
    return classify_record(rec) in ("jav-censored", "jav-uncensored")


def build_title(rec: dict, use_javdb: bool = False) -> dict:
    meta_id = make_meta_id(rec)
    category = classify_record(rec)
    online = uses_online_meta(rec)
    javdb_fields = javdb_meta(rec.get("titleno") or "", allow_network=use_javdb) if online else None
    poster, background = public_art(rec, javdb_fields)
    forum = forum_cover(rec)
    name = record_name(rec)
    magnet = (rec.get("magnet") or "").strip()
    title = {
        "id": meta_id,
        "type": "movie",
        "name": name,
        "poster": poster,
        "background": background,
        "releaseInfo": (rec.get("posted_at") or "")[:4],
        "description": record_description(rec),
        "runtime": rec.get("影片容量") or "",
        "infoHash": info_hash(magnet),
        "magnet": magnet,
        "category": category,
    }
    if forum:
        title["forumPoster"] = forum
    released = rfc3339_released((rec.get("posted_at") or "")[:10])
    if released:
        title["released"] = released
    meta = {
        "id": meta_id,
        "type": "movie",
        "name": name,
        "description": title["description"],
        "releaseInfo": title["releaseInfo"],
        "poster": poster,
        "background": background,
        "runtime": title["runtime"],
    }
    if released:
        meta["released"] = released
    cast = record_cast(rec)
    if cast:
        meta["cast"] = cast
        title["cast"] = cast
    if online:
        meta = enrich_meta(
            meta,
            rec.get("titleno") or "",
            rec=rec,
            allow_network=use_javdb,
            allow_jav321=use_javdb,
        )
    if meta.get("poster") and is_public_art(meta["poster"]):
        title["poster"] = meta["poster"]
    if meta.get("background") and is_public_art(meta["background"]):
        title["background"] = meta["background"]
    for key in ("name", "description", "cast", "genres", "director", "imdbRating", "runtime", "released"):
        if meta.get(key):
            title[key] = meta[key]
    released = rfc3339_released(title.get("released"))
    if released:
        title["released"] = released
        meta["released"] = released
    title["_meta"] = meta
    return title


def catalog_preview(title: dict) -> dict:
    item = {
        "id": title["id"],
        "type": "movie",
        "name": title["name"],
        "releaseInfo": title.get("releaseInfo") or "",
        "description": title.get("description") or "",
    }
    if title.get("poster"):
        item["poster"] = title["poster"]
    if title.get("cast"):
        item["cast"] = title["cast"]
    return item


def write_meta_and_stream(title: dict) -> None:
    meta = dict(title.get("_meta") or {})
    hints = meta.get("behaviorHints") or {}
    hints.setdefault("defaultVideoId", title["id"])
    hints.setdefault("hasScheduledVideos", False)
    meta["behaviorHints"] = hints
    if title.get("poster"):
        meta["poster"] = title["poster"]
    if title.get("background"):
        meta["background"] = title["background"]
    (ADDON_DIR / "meta" / "movie" / f"{title['id']}.json").write_text(
        json.dumps(as_meta_resource(meta), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    streams = []
    if title.get("infoHash"):
        streams.append(
            {
                "name": title.get("runtime") or "",
                "infoHash": title["infoHash"],
                "magnet": title.get("magnet") or "",
                "description": title.get("name") or "",
            }
        )
    (ADDON_DIR / "stream" / "movie" / f"{title['id']}.json").write_text(
        json.dumps({"streams": streams}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(use_javdb: bool = True) -> None:
    records = load_records()
    for sub in ("catalog/movie", "meta/movie", "stream/movie"):
        (ADDON_DIR / sub).mkdir(parents=True, exist_ok=True)
    TITLES_JSON.parent.mkdir(parents=True, exist_ok=True)

    manifest_catalogs = [
        {
            "type": "series" if catalog_id in HNT_CATALOG_IDS else "movie",
            "id": catalog_id,
            "name": name,
            "extra": catalog_extra(),
        }
        for catalog_id, name in CATALOGS
    ]
    manifest = {
        "id": ADDON_ID,
        "version": VERSION,
        "name": "Stremio JAV Database Addon",
        "description": f"Stremio JAV Database Addon {VERSION}. This is only a metadata scraper.",
        "logo": "https://www.stremio.com/website/stremio-logo-small.png",
        "resources": [
            {"name": "catalog", "types": ["movie", "series"]},
            {"name": "meta", "types": ["movie", "series"], "idPrefixes": ["jav:","hnt:"]},
            {"name": "stream", "types": ["movie", "series"], "idPrefixes": ["jav:","hnt:"]},
        ],
        "types": ["movie", "series"],
        "idPrefixes": ["jav:","hnt:"],
        "catalogs": manifest_catalogs,
        "behaviorHints": {"adult": True, "p2p": True},
    }
    (ADDON_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    grouped = newest_by_category(records)
    titles_out: dict[str, list[dict]] = {key: [] for key, _ in CATALOGS}
    written = 0
    for catalog_id, _name in CATALOGS:
        # hnt catalogs are series — their static files are written by the
        # series pass below, not the movie pass.
        if catalog_id in HNT_CATALOG_IDS:
            continue
        previews = []
        for rec in grouped.get(catalog_id, []):
            title = build_title(rec, use_javdb=use_javdb)
            write_meta_and_stream(title)
            written += 1
            previews.append(catalog_preview(title))
            titles_out[catalog_id].append(
                {key: value for key, value in title.items() if key != "_meta"}
            )
        (ADDON_DIR / "catalog" / "movie" / f"{catalog_id}.json").write_text(
            json.dumps({"metas": previews}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    # Old clients still request jav103; keep it as the censored first page.
    censored = ADDON_DIR / "catalog" / "movie" / "jav-censored.json"
    if censored.exists():
        (ADDON_DIR / "catalog" / "movie" / f"{CATALOG_ID}.json").write_text(
            censored.read_text(encoding="utf-8"), encoding="utf-8"
        )

    # --- hnt series (TV-show model) ---------------------------------------
    # hnt rows (Sukebei/Nyaa) are grouped into series collections:
    #   catalog/series/<catalog>.json  -> series metas
    #   meta/series/hnt:<key>.json    -> series meta with episodes
    #   stream/series/hnt:<key>:<n>.json -> per-episode streams
    for sub in ("catalog/series", "meta/series", "stream/series"):
        (ADDON_DIR / sub).mkdir(parents=True, exist_ok=True)
    series_map: dict[str, dict] = {}
    conn = sqlite3.connect(TITLES_SQLITE)
    try:
        for category in sorted(HNT_CATALOG_IDS):
            rows = conn.execute(
                """
                SELECT id, name, info_hash, magnet, poster, description, released, runtime, posted_at
                FROM titles WHERE category = ?
                """,
                (category,),
            ).fetchall()
            for row in rows:
                rid, name, info_hash_val, magnet, poster, description, released, runtime, posted_at = row
                nyaa_m = re.match(r"hnt:nyaa-(\d+)", rid or "")
                series_name, episode = parse_sukebei_name(name or "")
                series_name = str(series_name or "").strip()
                if series_name and not re.fullmatch(r"\d+", series_name):
                    key = slugify(series_name)
                    ep = episode if episode is not None else 1
                else:
                    key = f"solo-{nyaa_m.group(1) if nyaa_m else rid}"
                    ep = 1
                entry = series_map.setdefault(
                    key, {"category": category, "episodes": []}
                )
                entry["episodes"].append(
                    {
                        "id": f"hnt:{key}:{ep}",
                        "type": "episode",
                        "name": f"Episode {ep}",
                        "row_name": name or "",
                        "poster": poster or "",
                        "description": description or "",
                        "runtime": runtime or "",
                        "infoHash": info_hash_val or "",
                        "magnet": magnet or "",
                        "released": released or "",
                        "posted_at": posted_at or "",
                        "episode_num": ep,
                    }
                )
    finally:
        conn.close()

    catalog_metas: dict[str, list[dict]] = {}
    for key, entry in series_map.items():
        episodes = sorted(entry["episodes"], key=lambda e: e["episode_num"])
        # Series name: longest non-empty row name (mirrors querySeriesMeta).
        name = max(
            (e["row_name"] for e in episodes if e["row_name"]), key=len, default=f"hnt:{key}"
        )
        description = max((e["description"] for e in episodes), key=len, default="")
        poster = next((e["poster"] for e in episodes if e["poster"]), "")
        posted_at = max((e["posted_at"] for e in episodes if e["posted_at"]), default="")
        catalog_meta = {
            "id": f"hnt:{key}",
            "type": "series",
            "name": name,
            "description": description,
            "poster": poster,
            "episodes": len(episodes),
            "_category": entry["category"],
            "_posted_at": posted_at,
        }
        series_catalog = (
            "hentai-censored" if entry["category"] == "hentai-censored" else "hentai-uncensored"
        )
        catalog_metas.setdefault(series_catalog, []).append(catalog_meta)
        # stremio-core deserializes `videos` (not `episodes`), and each video
        # uses the Video fields: title / thumbnail / overview / season /
        # episode / released (RFC3339 — date-only strings break the client).
        series_meta = {
            "id": f"hnt:{key}",
            "type": "series",
            "name": name,
            "description": description,
            "poster": poster,
            "behaviorHints": {"defaultVideoId": f"hnt:{key}:1", "hasScheduledVideos": False},
            "videos": [
                {
                    "id": e["id"],
                    "type": "episode",
                    "title": e["name"],
                    "thumbnail": e["poster"],
                    "overview": e["description"],
                    "season": 1,
                    "episode": e["episode_num"],
                    "runtime": e["runtime"],
                    **({"released": e["released"]} if e["released"] else {}),
                }
                for e in episodes
            ],
        }
        (ADDON_DIR / "meta" / "series" / f"hnt:{key}.json").write_text(
            json.dumps(as_meta_resource(series_meta), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        for e in episodes:
            streams = []
            if e["infoHash"]:
                streams.append(
                    {
                        "name": e["runtime"] or "",
                        "infoHash": e["infoHash"],
                        "magnet": e["magnet"] or "",
                        "description": name,
                    }
                )
            (ADDON_DIR / "stream" / "series" / f"hnt:{key}:{e['episode_num']}.json").write_text(
                json.dumps({"streams": streams}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    for catalog_id, metas in catalog_metas.items():
        metas.sort(key=lambda m: (m.get("_posted_at") or "", m["id"]), reverse=True)
        metas = [
            {k: v for k, v in m.items() if not k.startswith("_")}
            for m in metas
        ]
        (ADDON_DIR / "catalog" / "series" / f"{catalog_id}.json").write_text(
            json.dumps({"metas": metas}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    TITLES_JSON.write_text(
        json.dumps(titles_out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    counts = ", ".join(f"{key}={len(titles_out[key])}" for key, _ in CATALOGS)
    print(f"addon: {written} catalog titles ({counts}) under {ADDON_DIR.relative_to(ROOT)}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Build the Stremio addon files")
    parser.add_argument("--no-javdb", action="store_true", help="Skip JavDB meta enrichment (offline build)")
    parser.add_argument("--sqlite-only", action="store_true", help="Write debrid-app/data/titles.sqlite from the scrape, no per-title files")
    parser.add_argument(
        "--enrich-sqlite",
        action="store_true",
        help="Apply JavDB cache to live SQLite, then fetch missing JAV newest-first",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max JavDB network fetches during --enrich-sqlite (0 = all missing)",
    )
    parser.add_argument(
        "--scrape-sqlite",
        action="store_true",
        help="Scrape sehuatang forum 103 newest-first, append missing titles to live SQLite, stop when caught up",
    )
    parser.add_argument(
        "--scrape-pages",
        type=int,
        default=0,
        help="Max sehuatang pages to read during --scrape-sqlite (0 = read until caught up)",
    )
    parser.add_argument(
        "--scrape-avgood",
        action="store_true",
        help="Scrape AvGood HK sections (c/612 三级 + c/595 四级), keep HD/Cantonese titles only",
    )
    parser.add_argument(
        "--avgood-pages",
        type=int,
        default=20,
        help="Max AvGood listing pages per section (0 = all pages)",
    )
    parser.add_argument(
        "--scrape-korean",
        action="store_true",
        help="Scrape iJavTorrent Korean tag (korean-305) into live SQLite (category 'korean', id 'kr:ijav-<id>')",
    )
    parser.add_argument(
        "--korean-pages",
        type=int,
        default=10,
        help="Max iJavTorrent korean tag pages (0 = until caught up)",
    )
    parser.add_argument(
        "--scrape-chinese",
        action="store_true",
        help="Scrape iJavTorrent Chinese tag (chinese-1543) into live SQLite (category 'chinese', id 'cn:ijav-<id>')",
    )
    parser.add_argument(
        "--chinese-pages",
        type=int,
        default=30,
        help="Max iJavTorrent chinese tag pages (0 = until caught up)",
    )
    parser.add_argument(
        "--scrape-hentai",
        action="store_true",
        help="Scrape Nyaa/Sukebei hentai (q=hentai -> 'hentai-censored', q=uncen -> 'hentai-uncensored', id 'hnt:nyaa-<id>')",
    )
    parser.add_argument(
        "--hentai-pages",
        type=int,
        default=5,
        help="Max Sukebei pages per hentai query",
    )
    parser.add_argument(
        "--free-set",
        action="store_true",
        help="During --enrich-sqlite, fetch JavDB only for the newest 15 titles per category (the free tier's meta set)",
    )
    args = parser.parse_args()
    if args.scrape_avgood:
        stats = scrape_avgood_hk(max_pages=args.avgood_pages)
        print(
            "avgood hk: "
            f"added={stats['added']} skipped={stats['skipped']} filtered={stats['filtered']} "
            f"blocked={stats['blocked']} hk_in_db={stats['hk_in_db']}"
        )
    if args.scrape_korean:
        stats = scrape_ijavtorrent("korean", max_pages=args.korean_pages)
        print(
            "ijavtorrent korean: "
            f"added={stats['added']} skipped={stats['skipped']} no_magnet={stats['no_magnet']} "
            f"pages={stats['pages']} total_in_db={stats['total_in_db']}"
        )
    if args.scrape_chinese:
        stats = scrape_ijavtorrent("chinese", max_pages=args.chinese_pages)
        print(
            "ijavtorrent chinese: "
            f"added={stats['added']} skipped={stats['skipped']} no_magnet={stats['no_magnet']} "
            f"pages={stats['pages']} total_in_db={stats['total_in_db']}"
        )
    if args.scrape_hentai:
        stats = scrape_nyaa_hentai(max_pages=args.hentai_pages)
        for category, _q in NYAA_HENTAI_QUERIES:
            s = stats[category]
            print(
                f"nyaa {category}: "
                f"added={s['added']} skipped={s['skipped']} no_magnet={s['no_magnet']} "
                f"pages={s['pages']} total_in_db={s['total_in_db']}"
            )
    if args.scrape_sqlite:
        stats = scrape_new_to_sqlite(use_javdb=not args.no_javdb, max_pages=args.scrape_pages)
        print(
            "scrape sqlite: "
            f"added={stats['added']} pages={stats['pages']} "
            f"caught_up_page={stats['caught_up_page']} total_in_db={stats['total_in_db']}"
        )
    elif args.enrich_sqlite:
        stats = enrich_live_sqlite(
            allow_network=not args.no_javdb,
            limit=args.limit,
            free_set=args.free_set,
        )
        print(
            "sqlite enrich: "
            f"cast_restored={stats['cast_restored']} cache_applied={stats['cache_applied']} "
            f"fetched={stats['fetched']} filled={stats['filled']} total={stats['total']}"
        )
    elif args.sqlite_only:
        titles = build_sqlite_titles(use_javdb=not args.no_javdb)
        dest = write_titles_sqlite(titles)
        print(f"sqlite: {len(titles)} titles -> {dest.relative_to(ROOT)}")
    else:
        main(use_javdb=not args.no_javdb)

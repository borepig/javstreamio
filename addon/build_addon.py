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
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from javdb import enrich_meta, javdb_meta

ROOT = Path(__file__).resolve().parent.parent
ADDON_DIR = ROOT / "addon"
TITLES_JSON = ROOT / "debrid-app" / "data" / "titles.json"
TITLES_SQLITE = ROOT / "debrid-app" / "data" / "titles.sqlite"
CATALOG_ID = "jav103"  # legacy alias of jav-censored
ADDON_ID = "community.javstremio"
VERSION = "0.4.38"
CATALOG_LIMIT = 200
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
PUBLIC_ART_HOSTS = ("pics.dmm.co.jp", "jdbstatic.com", "fourhoi.com", "image.mgstage.com")
BLOCKED_ART_HOSTS = ("vercel.app", "tu.djhdhs.us", "w4kmzkm.com")
CATALOGS = (
    ("jav-censored", "JAV Censored"),
    ("jav-uncensored", "JAV Uncensored"),
    ("western", "Western"),
    ("others", "Others"),
)


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


def classify_record(rec: dict) -> str:
    """Split scrape rows into jav-censored / jav-uncensored / western / others.

    Order: FC2PPV → others; JAV `XXXXX-NNN` uses 【是否有码】; English
    studio.date / actress names → western; everything else → others.
    """
    titleno = rec.get("titleno") or ""
    title = rec.get("title") or ""
    film = rec.get("影片名称") or ""
    blob = f"{titleno} {title} {film}"
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
              search_text TEXT DEFAULT ''
            )
            """
        )
        rows = [
            (
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
            )
            for title in titles
            if title.get("id") and title.get("infoHash")
        ]
        conn.executemany(
            """
            INSERT OR REPLACE INTO titles (
              id, category, name, description, release_info, released, runtime,
              poster, background, forum_poster, dmm_missing, fourhoi_missing,
              cast_json, genres_json, director, imdb_rating, info_hash, magnet,
              posted_at, search_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.execute("CREATE INDEX idx_titles_cat_posted ON titles(category, posted_at DESC, id DESC)")
        conn.commit()
    finally:
        conn.close()
    tmp.replace(dest)
    return dest


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


def newest_by_category(records: list[dict], limit: int = CATALOG_LIMIT) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {key: [] for key, _ in CATALOGS}
    for rec in records:
        grouped.setdefault(classify_record(rec), []).append(rec)
    for key, rows in grouped.items():
        rows.sort(key=lambda item: item.get("posted_at") or "", reverse=True)
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
        {"type": "movie", "id": catalog_id, "name": name, "extra": catalog_extra()}
        for catalog_id, name in CATALOGS
    ]
    manifest = {
        "id": ADDON_ID,
        "version": VERSION,
        "name": "Stremio JAV Database Addon",
        "description": f"Stremio JAV Database Addon {VERSION}. This is only a metadata scraper.",
        "logo": "https://www.stremio.com/website/stremio-logo-small.png",
        "resources": [
            {"name": "catalog", "types": ["movie"]},
            {"name": "meta", "types": ["movie"], "idPrefixes": ["jav:"]},
            {"name": "stream", "types": ["movie"], "idPrefixes": ["jav:"]},
        ],
        "types": ["movie"],
        "idPrefixes": ["jav:"],
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
    args = parser.parse_args()
    if args.sqlite_only:
        titles = build_sqlite_titles(use_javdb=not args.no_javdb)
        dest = write_titles_sqlite(titles)
        print(f"sqlite: {len(titles)} titles -> {dest.relative_to(ROOT)}")
    else:
        main(use_javdb=not args.no_javdb)

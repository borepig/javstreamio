#!/usr/bin/env python3
"""One-shot repair of the live JAV rows in debrid-app/data/titles.sqlite.

Fixes the three data-quality issues the user reported:
  1. Truncated cast names (e.g. ["神"] for 神木丽) -> restore full names from
     the forum JSONL records (data/*.jsonl).
  2. English JavDB genres -> convert to the Chinese genre names in the DB
     (via addon.javdb.translate_genres).
  3. Description starts with the "An magnet links sharing..." boilerplate ->
     replace with the actual forum synopsis line.

Idempotent: re-running only rewrites rows that actually change.
Usage: python3 .repair_jav_meta.py
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TITLES_SQLITE = ROOT / "debrid-app" / "data" / "titles.sqlite"
DATA_DIR = ROOT / "data"

sys.path.insert(0, str(ROOT / "addon"))
from javdb import translate_genres  # noqa: E402

UNSAFE_ID_RE = re.compile(r"[/\\?#]")
THREAD_ID_RE = re.compile(r"thread-(\d+)")
BOILERPLATE_RE = re.compile(r"an magnet links sharing", re.I)


def make_meta_id(rec: dict) -> str:
    no = (rec.get("titleno") or "").strip()
    if no:
        return "jav:" + UNSAFE_ID_RE.sub("~", no)
    m = THREAD_ID_RE.search(rec.get("url") or "")
    if m:
        return "jav:thread-" + m.group(1)
    return "jav:unknown"


def load_forum_records() -> dict:
    """meta_id -> record, from every data/*.jsonl (dedup by url/titleno)."""
    records: dict[str, dict] = {}
    for path in sorted(DATA_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not (rec.get("magnet") or "").strip():
                continue
            mid = make_meta_id(rec)
            if mid not in records:
                records[mid] = rec
    return records


def split_cast_names(raw: str) -> list:
    raw = (raw or "").strip().lstrip(".")
    if not raw or raw.upper() == "N/A":
        return []
    names = []
    for chunk in re.split(r"[/、,，\s]+", raw):
        chunk = chunk.strip()
        if chunk and chunk.upper() != "N/A":
            names.append(chunk)
    return names


def rebuild_description(rec: dict, current_desc: str) -> str:
    """Synopsis-first description from the forum record; fallback strips the
    boilerplate out of the existing description."""
    synopsis = (rec.get("title") or "").strip() if rec else ""
    if synopsis:
        parts = [synopsis]
        facts = []
        if (rec.get("影片容量") or "").strip():
            facts.append("容量: " + rec["影片容量"].strip())
        if (rec.get("是否有码") or "").strip():
            facts.append("有码/无码: " + rec["是否有码"].strip())
        if facts:
            parts.append("\n".join(facts))
        return "\n\n".join(parts)
    # No forum synopsis: just strip the boilerplate line from the existing text.
    lines = (current_desc or "").splitlines()
    kept = [ln for ln in lines if ln.strip() and not BOILERPLATE_RE.search(ln)]
    return "\n".join(kept).strip()


def jl(raw):
    try:
        v = json.loads(raw or "[]")
        return v if isinstance(v, list) else []
    except json.JSONDecodeError:
        return []


def is_truncated_cast(cast: list) -> bool:
    for n in cast:
        if re.fullmatch(r"[\u4e00-\u9fff]", n or ""):
            return True
    return False


def main() -> None:
    if not TITLES_SQLITE.exists():
        print(f"titles.sqlite not found at {TITLES_SQLITE}")
        sys.exit(1)
    records = load_forum_records()
    conn = sqlite3.connect(TITLES_SQLITE)
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute(
        "SELECT id, cast_json, genres_json, description FROM titles "
        "WHERE id LIKE 'jav:%'"
    ))

    cast_fixed = genre_fixed = desc_fixed = 0
    for row in rows:
        rec = records.get(row["id"])
        cast = jl(row["cast_json"])
        genres = jl(row["genres_json"])
        desc = row["description"] or ""

        new_cast = cast
        new_cast_src = None
        if rec is not None and is_truncated_cast(cast):
            new_cast = split_cast_names(rec.get("出演女优"))
            if new_cast:
                new_cast_src = new_cast

        new_genres = translate_genres(genres) if genres else []
        new_desc = desc
        if rec is not None or BOILERPLATE_RE.search(desc):
            new_desc = rebuild_description(rec, desc)

        changed = False
        if new_cast_src is not None and new_cast_src != cast:
            cast_fixed += 1
            changed = True
        if new_genres != genres:
            genre_fixed += 1
            changed = True
        if new_desc != desc:
            desc_fixed += 1
            changed = True
        if changed:
            conn.execute(
                "UPDATE titles SET cast_json=?, genres_json=?, description=? WHERE id=?",
                (
                    json.dumps(new_cast_src or cast, ensure_ascii=False),
                    json.dumps(new_genres, ensure_ascii=False),
                    new_desc,
                    row["id"],
                ),
            )
    conn.commit()
    conn.close()
    print(f"forum records loaded: {len(records)}")
    print(f"cast names restored: {cast_fixed}")
    print(f"genre lists translated: {genre_fixed}")
    print(f"descriptions fixed: {desc_fixed}")


if __name__ == "__main__":
    main()

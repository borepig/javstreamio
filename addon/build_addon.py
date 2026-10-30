"""Generate the Stremio addon files (manifest, catalog, meta, streams) from the scraped JSONL data.

Output layout (served by GitHub raw URLs once committed):
  addon/manifest.json
  addon/catalog/movie/jav103.json
  addon/meta/movie/jav:<titleno>.json
  addon/stream/movie/jav:<titleno>.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from javdb import enrich_meta

ROOT = Path(__file__).resolve().parent.parent
ADDON_DIR = ROOT / "addon"
CATALOG_ID = "jav103"
ADDON_ID = "community.javstreamio"
VERSION = "0.4.2"
HASH_RE = re.compile(r"urn:btih:([A-Za-z0-9]+)", re.I)
THREAD_ID_RE = re.compile(r"thread-(\d+)")


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


def make_meta_id(rec: dict) -> str:
    """Filename-safe id: use titleno if present, else the thread id from the URL."""
    no = (rec.get("titleno") or "").strip()
    if no:
        return "jav:" + no
    m = THREAD_ID_RE.search(rec.get("url") or "")
    if m:
        return "jav:thread-" + m.group(1)
    return "jav:unknown"


def main(use_javdb: bool = True) -> None:
    records = load_records()
    for sub in ("catalog/movie", "meta/movie", "stream/movie"):
        (ADDON_DIR / sub).mkdir(parents=True, exist_ok=True)

    manifest = {
        "id": ADDON_ID,
        "version": VERSION,
        "name": "JAV StreamIO",
        "description": "JAV catalog from sehuatang.org — titles, cast, and magnet streams",
        "logo": "https://www.stremio.com/website/stremio-logo-small.png",
        "resources": [
            {"name": "catalog", "types": ["movie"]},
            {"name": "meta", "types": ["movie"]},
            {"name": "stream", "types": ["movie"]},
        ],
        "types": ["movie"],
        "idPrefixes": ["jav:"],
        "catalogs": [
            {"type": "movie", "id": CATALOG_ID, "name": "JAV Catalog (forum 103)"}
        ],
        "behaviorHints": {"adult": True, "p2p": True},
    }
    (ADDON_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    catalog: list[dict] = []
    for rec in records:
        meta_id = make_meta_id(rec)
        poster = (rec.get("preview_images") or [""])[0]
        description = (
            f"出演女优: {rec.get('出演女优', '')}\n"
            f"容量: {rec.get('影片容量', '')}\n"
            f"有码/无码: {rec.get('是否有码', '')}"
        )
        catalog.append(
            {
                "id": meta_id,
                "type": "movie",
                "name": f"{rec.get('titleno', '')} {rec.get('title', '')}".strip(),
                "poster": poster,
                "releaseInfo": (rec.get("posted_at") or "")[:4],
                "description": description,
            }
        )
        meta = {
            "id": meta_id,
            "type": "movie",
            "name": f"{rec.get('titleno', '')} {rec.get('title', '')}".strip(),
            "description": description + f"\n来源: {rec.get('url', '')}",
            "releaseInfo": (rec.get("posted_at") or "")[:4],
            "poster": poster,
            "runtime": rec.get("影片容量") or "",
        }
        if use_javdb:
            meta = enrich_meta(meta, rec.get("titleno") or "")
        (ADDON_DIR / "meta" / "movie" / f"{meta_id}.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        streams = []
        magnet = (rec.get("magnet") or "").strip()
        hash = info_hash(magnet)
        if hash:
            streams.append(
                {
                    "name": f"{rec.get('影片容量', '')} {rec.get('是否有码', '')}".strip(),
                    "infoHash": hash,
                    "magnet": magnet,
                    "description": rec.get("titleno") or "",
                }
            )
        (ADDON_DIR / "stream" / "movie" / f"{meta_id}.json").write_text(
            json.dumps({"streams": streams}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    (ADDON_DIR / "catalog" / "movie" / f"{CATALOG_ID}.json").write_text(
        json.dumps({"metas": catalog}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"addon: {len(records)} items written under {ADDON_DIR.relative_to(ROOT)}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Build the Stremio addon files")
    parser.add_argument("--no-javdb", action="store_true", help="Skip JavDB meta enrichment (offline build)")
    args = parser.parse_args()
    main(use_javdb=not args.no_javdb)

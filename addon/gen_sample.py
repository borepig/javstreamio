"""Targeted generation: write meta + stream for a handful of JAVs whose JavDB info
is already in the local cache (no network). Appends them to the catalog so they show
in Stremio, then the user can verify metadata + Torbox/magnet link live."""
import json
import re
import sys
import urllib.request
import urllib.parse

sys.path.insert(0, "addon")
import javdb

ADDON_DIR = "addon"
HASH_RE = re.compile(r"urn:btih:([A-Za-z0-9]+)", re.I)

# dataset: titleno -> record (only those with a magnet)
recs = {}
for line in open("data/forum-103-all.jsonl", encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    r = json.loads(line)
    no = (r.get("titleno") or "").strip()
    if no and (r.get("magnet") or "").strip():
        recs[no] = r

cache = javdb._load_cache()

# current live catalog (v0.3.x on GitHub raw)
RAW = "https://raw.githubusercontent.com/borepig/javstreamio/main/addon"
def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")

cat = json.loads(get(RAW + "/catalog/movie/jav103.json"))
existing = {m["id"] for m in cat.get("metas", [])}

# pick up to 5 NEW titles: in cache + dataset, but NOT in the live catalog
new_ids = []
for no in cache:
    if no in recs and ("jav:" + no) not in existing:
        new_ids.append(no)
    if len(new_ids) >= 5:
        break
print("Picked new titles:", new_ids)

def info_hash(magnet_field):
    m = HASH_RE.search(magnet_field or "")
    return m.group(1).upper() if m else ""

catalog_metas = cat.get("metas", [])
added = []
for no in new_ids:
    rec = recs[no]
    meta_id = "jav:" + no
    poster = (rec.get("preview_images") or [""])[0]
    description = (
        f"出演女优: {rec.get('出演女优', '')}\n"
        f"容量: {rec.get('影片容量', '')}\n"
        f"有码/无码: {rec.get('是否有码', '')}"
    )
    meta = {
        "id": meta_id,
        "type": "movie",
        "name": f"{no} {rec.get('title', '')}".strip(),
        "description": description + f"\n来源: {rec.get('url', '')}",
        "releaseInfo": (rec.get("posted_at") or "")[:4],
        "poster": poster,
        "runtime": rec.get("影片容量") or "",
    }
    meta = javdb.enrich_meta(meta, no)  # cache-only (no network)
    with open(f"{ADDON_DIR}/meta/movie/{meta_id}.json", "w", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")

    magnet = (rec.get("magnet") or "").strip()
    h = info_hash(magnet)
    streams = []
    if h:
        streams.append(
            {
                "name": f"{rec.get('影片容量', '')} {rec.get('是否有码', '')}".strip(),
                "infoHash": h,
                "magnet": magnet,
                "description": no,
            }
        )
    with open(f"{ADDON_DIR}/stream/movie/{meta_id}.json", "w", encoding="utf-8") as f:
        f.write(json.dumps({"streams": streams}, ensure_ascii=False, indent=2) + "\n")

    catalog_metas.append(
        {
            "id": meta_id,
            "type": "movie",
            "name": f"{no} {rec.get('title', '')}".strip(),
            "poster": poster,
            "releaseInfo": (rec.get("posted_at") or "")[:4],
            "description": description,
        }
    )
    added.append(meta_id)

with open(f"{ADDON_DIR}/catalog/movie/jav103.json", "w", encoding="utf-8") as f:
    f.write(json.dumps({"metas": catalog_metas}, ensure_ascii=False, indent=2) + "\n")

print("Wrote meta + stream for:", added)
print("Catalog now has", len(catalog_metas), "metas (was", len(existing), "+", len(added), "new)")

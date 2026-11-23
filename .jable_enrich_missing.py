"""Batch Jable enrich for the remaining missing JAV titles.

Reuses the QNAP Firecrawl scrape + markdown parsing from
.jable_enrich_silk110.py, but loops over a list of codes and writes
only rows that Jable actually has data for. Usage:
  python3 .jable_enrich_missing.py
"""
import os, re, json
import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "debrid-app", "data", "titles.sqlite")
FC = "http://192.168.68.83:3002"
TODAY_Y = 2026

# Remaining 10-target set minus the 5 already filled. JavDB 番號 mismatches:
# MDB-365 (JavDB has MDBK-365), KIL-009 (KIL-109), KIL-004 (KIL-104),
# FAA-068 (FAA-268); VEC-498-4k not_found on JavDB.
CODES = ["MDB-365", "KIL-009", "KIL-004", "FAA-068", "VEC-498-4k"]


def scrape(url):
    import time
    for attempt in range(3):
        try:
            r = requests.post(FC + "/v2/scrape", json={"url": url, "formats": ["markdown"]}, timeout=240)
            data = (r.json() or {}).get("data") or {}
            md = data.get("markdown")
            if md:
                return md
        except Exception as e:
            print("attempt %d failed: %s" % (attempt + 1, e))
            time.sleep(3 * (2 ** attempt))
    return None


def parse(md):
    title = ""
    mt = re.search(r"^####\s+([^\n]+)", md, re.M)
    if mt:
        title = mt.group(1).strip()
    cast = re.findall(r"\[([^\]]+)\]\(https://en\.jable\.tv/models/[a-f0-9]+/\)", md)
    genres = re.findall(r"\[([^\]]+)\]\(https://en\.jable\.tv/(?:categories|tags)/[^/]+/\)", md)
    released = ""
    mr = re.search(r"^######\s+(\d+)\s+years ago", md, re.M)
    if mr:
        year = TODAY_Y - int(mr.group(1))
        released = f"{year}-11-23T00:00:00.000Z"
    return title, cast, genres, released


import sqlite3
con = sqlite3.connect(DB)

for code in CODES:
    rid = f"jav:{code}"
    row = con.execute("SELECT id FROM titles WHERE id=?", (rid,)).fetchone()
    if not row:
        print(f"{code}: row not found in DB — skip")
        continue
    md = scrape(f"https://en.jable.tv/videos/{code}/")
    if not md:
        print(f"{code}: JABLE FETCH FAILED")
        continue
    title, cast, genres, released = parse(md)
    if not (cast or genres):
        print(f"{code}: NO USABLE FIELDS — leaving row unchanged")
        continue
    con.execute(
        "UPDATE titles SET genres_json=?, cast_json=?, released=? WHERE id=?",
        (json.dumps(genres, ensure_ascii=False), json.dumps(cast, ensure_ascii=False), released, rid),
    )
    con.commit()
    print(f"WROTE {rid} genres={len(genres)} cast={len(cast)} released={released}")
print("DONE")

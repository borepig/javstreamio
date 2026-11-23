"""One-shot Jable enrich for SILK-110 (only Jable-available of the 6 remaining).

Fetches https://en.jable.tv/videos/SILK-110/ through the QNAP Firecrawl
proxy (Jable direct GET is Cloudflare 403). Jable page layout (verified):

  #### SILK-110 Midnight Egoists
  ###### 4 years ago 963 977
  [二](.../models/...) [上](...) [一](...)          <- cast (models)
  ##### [Roleplay](.../categories/roleplay/)
   [Chinese Subtitle](.../categories/chinese-subtitle/)
   • [Girl](.../tags/girl/) [Big tits](...) ...     <- genres (categories + tags)

Parses title / cast / genres / released and writes them into the live
SQLite row.
"""
import os, re, json
import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "debrid-app", "data", "titles.sqlite")
FC = "http://192.168.68.83:3002"
CODE = "SILK-110"
TODAY_Y = 2026  # session date 2026-11-23

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

md = scrape(f"https://en.jable.tv/videos/{CODE}/")
if not md:
    print("JABLE FETCH FAILED")
    raise SystemExit(1)

# Title: the '#### <title>' line.
title = ""
mt = re.search(r"^####\s+([^\n]+)", md, re.M)
if mt:
    title = mt.group(1).strip()

# Cast: markdown links to /models/ — label is the model (actress) name.
cast = re.findall(r"\[([^\]]+)\]\(https://en\.jable\.tv/models/[a-f0-9]+/\)", md)

# Genres: markdown links to /categories/ and /tags/ (labels).
genres = re.findall(r"\[([^\]]+)\]\(https://en\.jable\.tv/(?:categories|tags)/[^/]+/\)", md)

# Released: '###### N years ago' -> approximate year.
released = ""
mr = re.search(r"^######\s+(\d+)\s+years ago", md, re.M)
if mr:
    years_ago = int(mr.group(1))
    year = TODAY_Y - years_ago
    released = f"{year}-11-23T00:00:00.000Z"

print("title:", title[:60])
print("cast:", cast)
print("genres:", genres)
print("released:", released)

if not (cast or genres):
    print("NO USABLE FIELDS — leaving row unchanged")
    raise SystemExit(2)

import sqlite3
con = sqlite3.connect(DB)
con.execute(
    "UPDATE titles SET genres_json=?, cast_json=?, released=? WHERE id=?",
    (
        json.dumps(genres, ensure_ascii=False),
        json.dumps(cast, ensure_ascii=False),
        released,
        f"jav:{CODE}",
    ),
)
con.commit()
print("WROTE jav:%s genres=%d cast=%d released=%s" % (CODE, len(genres), len(cast), released))

"""Enrich Hong Kong level-3 AV titles by movie-name search.

The `hongkong` rows (AvGood HK AV) currently carry an AvGood listing date in
`released` (when the item was posted), not the film's real release year. This
script recovers the film's release year two ways, per the user's request to "use
name searches":

1. Free pass (no network): if the stored name already contains a 4-digit year
   (e.g. "咸湿唐伯虎 1987"), set `released` to that year.
2. Network pass: for titles without an in-name year, query the local SearXNG
   instance with the cleaned movie name (movie engines: imdb/tmdb/wikipedia/
   baidu/sogou/quark), then fetch the top movie-site hit and parse the release
   year and a synopsis paragraph into the DB.

Progress is tracked in `addon/.hk_websearch_done.json` (list of processed ids) so
the job is resume-safe.

Usage (from repo root):
    python3 addon/websearch_hk.py [--limit N] [--no-fetch]
"""

import html as html_module
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

from build_addon import TITLES_SQLITE, title_search_text

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DONE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".hk_websearch_done.json")
SXNG = os.environ.get("SXNG_URL", "http://192.168.68.83:8888")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
DELAY = float(os.environ.get("WEBSEARCH_DELAY", "0.5"))
# Only `google cse` and `wikipedia` are enabled on the local SearXNG instance;
# imdb/tmdb/baidu/sogou/quark are disabled there, so the old filter returned 0 results.
HK_ENGINES = "google cse,wikipedia"
LIMIT = 0
FETCH_TOP = True
CURRENT_YEAR = int(time.strftime("%Y"))

STRIP_BRACKETS = [re.compile(r"\[[^\]]*\]"), re.compile(r"【[^】]*】")]
STRIP_WORDS = ["国语中字", "粤语中字", "国语", "粤语", "增强版", "AI修复版",
                "AI修复", "高清", "720P", "1080P", "4K", "BluRay", "bluray", "MP4", "MKV"]


def clean_hk_name(name):
    """Strip quality/definition suffixes from an AvGood HK title to get the core film name."""
    s = name or ""
    for pat in STRIP_BRACKETS:
        s = pat.sub(" ", s)
    for w in STRIP_WORDS:
        s = s.replace(w, " ")
    return re.sub(r"\s+", " ", s).strip()


def year_in_name(name):
    for m in re.finditer(r"\b(19|20)\d{2}\b", name or ""):
        y = int(m.group(0))
        if 1980 <= y <= CURRENT_YEAR:
            return y
    return None


def sxng_search(query, engines=HK_ENGINES):
    params = {"q": query, "format": "json"}
    if engines:
        params["engines"] = engines
    url = f"{SXNG}/search?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "javstreamio/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except Exception as e:
        print(f"  sxng query '{query}' failed: {e}", file=sys.stderr)
        return []
    out = []
    for res in data.get("results", []):
        out.append({
            "title": res.get("title") or "",
            "content": res.get("content") or "",
            "url": res.get("url") or "",
            "engine": res.get("engine") or "",
        })
    return out


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def page_text(html):
    """Crude visible-text extraction for year/synopsis parsing."""
    t = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    t = re.sub(r"<style.*?</style>", "", t, flags=re.S)
    t = re.sub(r"<[^>]+>", "\n", t)
    t = re.sub(r"\n{2,}", "\n", t)
    return html_module.unescape(t).strip()


def extract_year(text):
    for m in re.finditer(r"\b(19|20)\d{2}\b", text or ""):
        y = int(m.group(0))
        if 1980 <= y <= CURRENT_YEAR:
            return y
    return None


def extract_synopsis(text):
    """Heuristic: find a reasonably long non-boilerplate paragraph as the synopsis."""
    for para in re.split(r"\n{2,}", (text or "").strip()):
        para = re.sub(r"\s+", " ", para).strip()
        if len(para) >= 40 and not para.startswith(("©", "Note:", "Last update")):
            return para
    return ""


def main():
    global LIMIT, FETCH_TOP
    args = sys.argv[1:]
    if "--limit" in args:
        LIMIT = int(args[args.index("--limit") + 1])
    FETCH_TOP = "--no-fetch" not in args

    done = set()
    if os.path.exists(DONE_FILE):
        done = set(json.load(open(DONE_FILE, encoding="utf-8")))

    conn = sqlite3.connect(TITLES_SQLITE)
    rows = conn.execute(
        "SELECT id, name FROM titles WHERE category = 'hongkong' ORDER BY posted_at DESC"
    ).fetchall()
    if LIMIT:
        rows = rows[:LIMIT]

    updated = year_from_name = searched = failed = no_year = skipped = 0
    t0 = time.time()
    new_done = list(done)
    for i, (tid, name) in enumerate(rows, 1):
        if tid in done:
            skipped += 1
            continue
        core = clean_hk_name(name)
        year = year_in_name(name)
        released = ""
        synopsis = ""
        if year:
            released = f"{year}-01-01T00:00:00.000Z"
            year_from_name += 1
        elif FETCH_TOP and core:
            results = sxng_search(core)
            if results:
                best = results[0]
                searched += 1
                text = best["content"]
                if best["url"]:
                    try:
                        text += "\n" + page_text(http_get(best["url"]))
                    except Exception as e:
                        print(f"  {tid}: page fetch failed ({e})", file=sys.stderr)
                y = extract_year(text)
                if y:
                    released = f"{y}-01-01T00:00:00.000Z"
                synopsis = extract_synopsis(text)
        else:
            failed += 1

        description_parts = []
        if synopsis:
            description_parts.append(synopsis)
        description_parts.append(f"Source: {('AvGood (香港三级)' if not released else 'AvGood (香港三级) / web search')}")
        description = "\n\n".join(description_parts).strip()
        search_text = title_search_text({"id": tid, "name": core or name, "genres": ["Hong Kong AV"]})
        if released:
            conn.execute(
                "UPDATE titles SET released = ?, description = ?, search_text = ? WHERE id = ?",
                (released, description, search_text, tid),
            )
            conn.commit()   # per-row commit so a concurrent job on the same SQLite doesn't get locked out
            updated += 1
            new_done.append(tid)   # only mark done when a real release year was found
        else:
            no_year += 1          # no year recovered; stays pending for a later run
        if i % 25 == 0 or i == len(rows):
            print(f"  {i}/{len(rows)} (updated={updated}, name-year={year_from_name}, "
                  f"searched={searched}, no-year={no_year}) [{time.time() - t0:.0f}s]")
        time.sleep(DELAY)

    conn.commit()
    conn.close()
    with open(DONE_FILE, "w", encoding="utf-8") as f:
        json.dump(new_done, f, indent=2)
    print(f"websearch_hk: updated={updated}, name-year={year_from_name}, "
            f"searched={searched}, no-year={no_year} ({time.time() - t0:.0f}s); done={len(new_done)}")


if __name__ == "__main__":
    main()

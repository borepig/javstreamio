"""Enrich the remaining Chinese AV codes in the live SQLite via SearXNG web search.

Reads `addon/.chinese_websearch_codes.json` (a list of `{"id","code"}`). For each
code that has not been enriched yet, it queries the local SearXNG instance
(`http://192.168.68.83:8888`), extracts the real title and release date from the
results (and, with `--fetch`, the top result's page), writes them into the
`titles` row, and removes the code from the state files so the job is resume-safe.

Usage (from repo root):
    python3 addon/websearch_chinese.py [--limit N] [--fetch] [--no-fetch]

The job is resumable: already-enriched rows (name != code, or description already
carries a "Source:" line) are skipped and marked done.
"""

import html as html_module
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from build_addon import TITLES_SQLITE, title_search_text

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".chinese_websearch_codes.json")
PENDING_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".chinese_websearch_pending.json")

# Local self-hosted SearXNG meta-search (free, unlimited).
SXNG = os.environ.get("SXNG_URL", "http://192.168.68.83:8888")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
DELAY = float(os.environ.get("WEBSEARCH_DELAY", "0.6"))
FETCH_TOP = False
LIMIT = 0
CURRENT_YEAR = int(time.strftime("%Y"))

# Exponential backoff for transient 403/429/5xx: 15s, 30s, 60s, 120s, 240s, capped at 300s.
BACKOFF_BASE = 15
BACKOFF_CAP = 300

def backoff_sleep(attempt):
    """Sleep with exponential backoff (capped). `attempt` is the zero-based retry index."""
    time.sleep(min(BACKOFF_BASE * (2 ** attempt), BACKOFF_CAP))

def sxng_search(query, engines=None, max_retries=4):
    """Query the SearXNG instance; return a list of {title, content, url, engine}.
    Transient 429/5xx and socket errors back off and retry (a success resets the
    streak on the next call); non-retryable HTTP errors fail fast."""
    params = {"q": query, "format": "json"}
    if engines:
        params["engines"] = engines
    url = f"{SXNG}/search?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "javstreamio/1.0"})
    data = None
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 or 500 <= e.code < 600:
                if attempt == max_retries:
                    print(f"  sxng query '{query}' still HTTP {e.code} after backoff", file=sys.stderr)
                    return []
                backoff_sleep(attempt)
                continue
            print(f"  sxng query '{query}' HTTP {e.code} (non-retryable)", file=sys.stderr)
            return []
        except Exception as e:
            if attempt == max_retries:
                print(f"  sxng query '{query}' failed: {e}", file=sys.stderr)
                return []
            backoff_sleep(attempt)
            continue
    out = []
    for res in (data or {}).get("results", []):
        out.append({
            "title": res.get("title") or "",
            "content": res.get("content") or "",
            "url": res.get("url") or "",
            "engine": res.get("engine") or "",
        })
    return out


def http_get(url, max_retries=3):
    """GET with exponential backoff on transient 403/429/5xx and socket errors;
    other HTTP errors (e.g. 404) raise immediately so the caller can skip that row."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) or 500 <= e.code < 600:
                if attempt == max_retries:
                    raise
                backoff_sleep(attempt)
                continue
            raise
        except Exception:
            if attempt == max_retries:
                raise
            backoff_sleep(attempt)


def clean_title(raw, code):
    """Strip SearXNG title noise (leading '+++', trailing ' :: engine' / ' - SiteName')."""
    t = (raw or "").strip()
    t = re.sub(r"^\u002b{1,}\s*", "", t)          # leading '+++'
    t = re.sub(r"\s*::\s*[\w.\- ]+$", "", t)      # trailing ' :: Engine'
    t = re.sub(r"\s*-\s*[^-]+$", "", t)            # trailing ' - SiteName'
    return html_module.unescape(t).strip()


def extract_rfc3339(text):
    """Find a plausible release date/year in `text` and return an RFC3339 value, or ''."""
    m = re.search(r"\b((19|20)\d{2})-(\d{2})-(\d{2})\b", text)
    if m and 1980 <= int(m.group(1)) <= CURRENT_YEAR:
        return f"{m.group(1)}-{m.group(3)}-{m.group(4)}T00:00:00.000Z"
    for m in re.finditer(r"\b(19|20)\d{2}\b", text):
        y = int(m.group(0))
        if 1980 <= y <= CURRENT_YEAR:
            return f"{m.group(0)}-01-01T00:00:00.000Z"
    return ""


def already_enriched(name, description, code):
    """A row counts as enriched when its name is a real title (not just the code),
    or its description carries the web-search source marker. (The default iJavTorrent
    description already says 'Source: iJavTorrent (Chinese AV)', so the plain 'Source:'
    substring is NOT a reliable done-signal.)"""
    n = (name or "").strip()
    if n and n.lower() != code.lower():
        return True
    if description and "Source: web search" in description:
        return True
    return False


def pick_best(results, code):
    """Return the first result whose title contains the code (a structured AV-DB hit),
    or None if no result carries the code. (Falling back to results[0] produced
    junk titles like government documents.)"""
    for r in results:
        if code.lower() in (r["title"] or "").lower() and (r["title"] or "").strip():
            return r
    return None


def run():
    global LIMIT, FETCH_TOP
    args = sys.argv[1:]
    if "--limit" in args:
        LIMIT = int(args[args.index("--limit") + 1])
    FETCH_TOP = "--fetch" in args and "--no-fetch" not in args

    codes = json.load(open(CODES_FILE, encoding="utf-8"))
    if LIMIT:
        codes = codes[:LIMIT]

    conn = sqlite3.connect(TITLES_SQLITE)
    done_ids = set()
    updated = skipped = failed = 0
    t0 = time.time()
    for i, entry in enumerate(codes, 1):
        tid, code = entry["id"], (entry.get("code") or "").strip()
        row = conn.execute("SELECT name, description FROM titles WHERE id = ?", (tid,)).fetchone()
        if row and already_enriched(row[0], row[1], code):
            done_ids.add(tid)
            skipped += 1
            continue
        results = sxng_search(code)
        best = pick_best(results, code)
        # If the plain code query has no title carrying the code, retry with the
        # Chinese AV keyword so low-presence codes (e.g. xb-*) still resolve.
        if best is None:
            results2 = sxng_search(code + " 片")
            best = pick_best(results2, code)
        if best is None:
            failed += 1
            print(f"  {tid} ({code}): no result title carries the code; left for a later run")
            time.sleep(DELAY)
            continue
        title = clean_title(best["title"], code) or code
        # Release-date text: the hit's own snippet + (optionally) the hit's page.
        date_texts = [best["content"] or ""]
        if FETCH_TOP and best["url"]:
            try:
                date_texts.append(http_get(best["url"]))
            except Exception as e:
                print(f"  {tid}: top-page fetch failed ({e})", file=sys.stderr)
        released = extract_rfc3339("\n".join(date_texts))
        description = f"{title}\n\nSource: web search (SearXNG: {best['engine']})"
        search_text = title_search_text({"id": tid, "name": title, "genres": ["Chinese AV"]})
        if released:
            conn.execute(
                "UPDATE titles SET name = ?, description = ?, released = ?, search_text = ? WHERE id = ?",
                (title, description, released, search_text, tid),
            )
        else:
            conn.execute(
                "UPDATE titles SET name = ?, description = ?, search_text = ? WHERE id = ?",
                (title, description, search_text, tid),
            )
        conn.commit()
        updated += 1
        done_ids.add(tid)
        if i % 20 == 0 or i == len(codes):
            print(f"  {i}/{len(codes)} (updated={updated}, skipped={skipped}, failed={failed}) "
                  f"[{time.time() - t0:.0f}s]")
        time.sleep(DELAY)

    conn.close()

    # Mark done: remove finished ids from both state files.
    remaining_codes = [e for e in json.load(open(CODES_FILE, encoding="utf-8"))]
    remaining_pending = [x for x in json.load(open(PENDING_FILE, encoding="utf-8"))]
    if done_ids:
        remaining_codes = [e for e in remaining_codes if e.get("id") not in done_ids]
        remaining_pending = [x for x in remaining_pending if x not in done_ids]
        with open(CODES_FILE, "w", encoding="utf-8") as f:
            json.dump(remaining_codes, f, indent=2)
        with open(PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump(remaining_pending, f, indent=2)
    print(f"websearch_chinese: updated={updated}, skipped={skipped}, failed={failed} "
            f"({time.time() - t0:.0f}s); remaining codes={len(remaining_codes)}")


if __name__ == "__main__":
    run()

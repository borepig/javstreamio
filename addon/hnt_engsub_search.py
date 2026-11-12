"""Sukebei eng-sub magnet search for hnt episodes on PikPak.

Stands on the "eng-sub priority, else available" rule: for every hnt
episode row that has a debrid magnet but no English-sub marker, search
Sukebei (sukebei.nyaa.si, anime-video category) once per unique
series; an eng-subbed result (Japanese audio + English subtitles) swaps
the row's magnet/info_hash. If Sukebei has no eng-subbed torrent for the
series, the existing magnet is kept as the fallback.

Resume-safe: rows whose magnet already carries an English-sub marker are
skipped, so re-running only processes the remaining targets.

Usage:
  python3 addon/hnt_engsub_search.py                 # full run (writes DB)
  python3 addon/hnt_engsub_search.py --dry-run        # print the plan, no writes
  python3 addon/hnt_engsub_search.py --limit 5        # first 5 series only
"""
import argparse
import html as html_module
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DB = "debrid-app/data/titles.sqlite"
SUKUBEI_BASE = "https://sukebei.nyaa.si"
DELAY = 2.5
BACKOFFS = [15, 30, 60, 120, 300]
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"

# Any of these in a torrent name means the torrent ships English subtitles.
ENG_SUB_RE = re.compile(r"eng-?sub|english\s*(/\s*espa[nñ]ol)?\s*subs?|english\s*subbed|eng\s*sub|en\s*sub", re.I)


def http_get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def sukebei_search(query: str, page: int = 1) -> list[dict]:
    """Search Sukebei's anime-video category and parse the listing rows."""
    q = urllib.parse.quote(query, safe="")
    url = f"{SUKUBEI_BASE}/?f=0&c=0_0&q={q}" + (f"&page={page}" if page > 1 else "")
    html = http_get(url)
    items = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        view_m = re.search(r'href="/view/(\d+)"', row)
        if not view_m:
            continue
        title_m = re.search(r'<td colspan="2">\s*<a[^>]*>(.*?)</a>', row, re.S)
        magnet_m = re.search(r'href="(magnet:\?xt=urn:btih:[^"]+)"', row)
        items.append({
            "id": view_m.group(1),
            "name": re.sub(r"<[^>]+>", "", title_m.group(1)).strip() if title_m else "",
            "magnet": html_module.unescape(magnet_m.group(1)) if magnet_m else "",
        })
    return items


def magnet_hash(magnet: str) -> str:
    m = re.search(r"xt=urn:btih:([0-9a-fA-F]{40})", magnet or "")
    return m.group(1).upper() if m else ""


def ep_tokens(ep: int):
    """Episode-number forms a Sukebei torrent name might use."""
    return {f"E{ep:02d}", f"E{ep}", f"EP{ep}", f"EP{ep:02d}", f" - {ep}", f" - {ep:02d}"}


def pick_engsub(results: list[dict], target_eps: list[int]):
    """Pick eng-subbed results and map them onto the target episodes.

    Priority: exact per-episode eng-sub result first, then a batch/complete
    eng-sub result for the whole series, then a single eng-sub OVA when the
    series has exactly one target episode."""
    engsubs = [r for r in results if r["magnet"] and ENG_SUB_RE.search(r["name"])]
    if not engsubs:
        return {}
    mapping = {}
    batch_re = re.compile(r"batch|all\s+episodes|complete", re.I)
    batch = [r for r in engsubs if batch_re.search(r["name"])]
    for ep in target_eps:
        tokens = ep_tokens(ep)
        exact = next(
            (r for r in engsubs if any(t in r["name"].upper().replace("-", " ") or t in r["name"] for t in tokens)),
            None,
        )
        if exact:
            mapping[ep] = exact
        elif batch and len(batch):
            mapping[ep] = batch[0]
        elif len(target_eps) == 1:
            mapping[ep] = engsubs[0]
    return mapping


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="Process at most N series (0 = all)")
    ap.add_argument("--dry-run", action="store_true", help="Print the mapping plan without writing to the DB")
    ap.add_argument("--delay", type=float, default=DELAY)
    args = ap.parse_args()

    db = sqlite3.connect(DB)
    cur = db.cursor()
    cols = {r[1] for r in cur.execute("PRAGMA table_info(titles)")}
    need = {"magnet", "info_hash"}
    if not need.issubset(cols):
        missing = need - cols
        for c in missing:
            cur.execute(f"ALTER TABLE titles ADD COLUMN {c} TEXT DEFAULT ''")
        db.commit()

    # Targets: hnt episode rows with a magnet lacking an eng-sub marker.
    rows = cur.execute(
        "SELECT id, series_key, episode_num, magnet FROM titles"
        " WHERE category LIKE 'hentai%' AND episode_num > 0 AND info_hash != ''"
    ).fetchall()
    targets = [r for r in rows if not ENG_SUB_RE.search(r[3] or "")]
    by_series = {}
    for rid, key, ep, mag in targets:
        by_series.setdefault(key, []).append((rid, ep, mag))
    series = sorted(by_series)
    if args.limit:
        series = series[: args.limit]

    # Series display name drives the Sukebei query.
    name_by_key = {
        r[0]: r[1] for r in cur.execute(
            "SELECT series_key, name FROM titles WHERE episode_num = 0"
        )
    }

    swapped = no_sub = failed = 0
    for i, key in enumerate(series):
        name = name_by_key.get(key) or key
        try:
            results = sukebei_search(name)
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                # Throttled: exponential backoff (15s, 30s, 60s, ... cap 300s)
                # and keep going — never abort the job.
                for attempt, wait in enumerate(BACKOFFS, 1):
                    print(f"[{key}] Sukebei {e.code}; backing off {wait}s", file=sys.stderr)
                    time.sleep(wait)
                    try:
                        results = sukebei_search(name)
                        break
                    except urllib.error.HTTPError:
                        continue
                else:
                    failed += 1
                    continue
            else:
                failed += 1
                print(f"[{key}] Sukebei fetch failed ({e})", file=sys.stderr)
                continue
        time.sleep(args.delay)

        eps = sorted(ep for _, ep, _ in by_series[key])
        mapping = pick_engsub(results, eps)
        if not mapping:
            no_sub += 1
            n_eng = sum(1 for r in results if ENG_SUB_RE.search(r["name"]))
            print(f"[{key}] {len(results)} Sukebei results, {n_eng} eng-sub; keeping fallback magnets for eps {','.join(map(str, eps))}")
            continue
        for ep, res in sorted(mapping.items()):
            rid = next(r for r in by_series[key] if r[1] == ep)[0]
            new_hash = magnet_hash(res["magnet"])
            if not new_hash:
                continue
            if args.dry_run:
                print(f"dry: {rid} <- {res['name'][:80]}")
            else:
                cur.execute(
                    "UPDATE titles SET magnet = ?, info_hash = ? WHERE id = ?",
                    (res["magnet"], new_hash, rid),
                )
                swapped += 1
        if not args.dry_run:
            db.commit()
        n_eng = sum(1 for r in results if ENG_SUB_RE.search(r["name"]))
        print(f"[{key}] eng-sub swap for {len(mapping)} of {len(eps)} target episodes ({len(results)} Sukebei results, {n_eng} eng-sub)")

    if not args.dry_run:
        db.commit()
        db.close()
    print(f"done: swapped={swapped} series_no_engsub={no_sub} fetch_failed={failed}")


if __name__ == "__main__":
    main()

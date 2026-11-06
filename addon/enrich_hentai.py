#!/usr/bin/env python3
"""Enrich the hnt:* (Sukebei/Nyaa) hentai rows in the live SQLite DB.

Sources:
- Metadata: hanime.tv guest API (full-series list, cached in .hanime_hvs.json).
  Gives name, multilingual search_titles, HTML description, tags, cover/poster
  (hanime-cdn.com), studio (brand) and RFC3339 release dates.
- Direct playback: Hentaistream episode pages embed a player frame whose HTML
  carries the full-episode mp4 on cdn2.streamhentai.org (video/mp4, byte ranges).

Resume-safe: the hanime list and found stream URLs are cached to JSON state
files; re-running only refetches what is missing.

Usage:
  python3 addon/enrich_hentai.py                 # full run
  python3 addon/enrich_hentai.py --limit 20      # dry-ish small batch
  python3 addon/enrich_hentai.py --no-play       # skip hentaistream direct-playback
  python3 addon/enrich_hentai.py --refresh        # refetch the hanime list
"""

import argparse
import html as html_mod
import json
import os
import re
import sqlite3
import time
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "debrid-app", "data", "titles.sqlite")
HVS_CACHE = os.path.join(ROOT, "addon", ".hanime_hvs.json")
STREAM_CACHE = os.path.join(ROOT, "addon", ".hentai_stream_cache.json")

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
HVS_URL = "https://guest.freeanimehentai.net/api/v11/search_hvs"
HENTAI_BASE = "https://tube.hentaistream.com"
NYAA_VIEW_BASE = "https://sukebei.nyaa.si"

HVS_DELAY = 0.8   # seconds between hentaistream page fetches
FRAME_DELAY = 0.3  # seconds between frame fetches
VIEW_DELAY = 0.5   # seconds between sukebei view fetches


def http_get(url, referer=None):
    headers = {"User-Agent": UA}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def load_hvs(refresh=False):
    if not refresh and os.path.exists(HVS_CACHE):
        with open(HVS_CACHE, encoding="utf-8") as f:
            items = json.load(f)
        if items:
            return items
    data = json.loads(http_get(HVS_URL, referer="https://hanime.tv/"))
    items = data.get("data") or []
    os.makedirs(os.path.dirname(HVS_CACHE), exist_ok=True)
    with open(HVS_CACHE, "w", encoding="utf-8") as f:
        json.dump(items, f)
    return items


def load_stream_cache():
    if os.path.exists(STREAM_CACHE):
        try:
            with open(STREAM_CACHE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_stream_cache(cache):
    with open(STREAM_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f)


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s or "").lower().strip()).strip("-")


def parse_sukebei_name(name):
    """Split a Sukebei/Nyaa title into (series, episode)."""
    t = re.sub(r"^\[[^\]]*\]\s*", "", str(name).strip())       # [Group]
    t = re.sub(r"\[[^\]]*\]", " ", t)                              # [720p OV WEB-DL] etc.
    for tok in ("1080p", "720p", "480p", "UNCENSORED", "CENSORED",
                 "OV", "OVA", "WEB-DL", "AAC2.0", "H.264", "H.265",
                 "English Audio", "Final", "AVC", "x264", "VOSTFR",
                 "DUAL", "FLAC2.0", "SUBBED"):
        # Word-bounded so the quality token "OV" cannot shred series
        # names that contain "ov" (e.g. "Overflow" -> "erflow").
        t = re.sub(r"(?i)\b" + re.escape(tok) + r"\b", " ", t)
    m = re.search(r"S(\d+)E(\d+)", t, re.I)
    episode = int(m.group(2)) if m else None
    if episode is None:
        m2 = re.search(r"\bE(\d+)\b", t) or re.search(r"\bEpisode\s*(\d+)", t, re.I)
        episode = int(m2.group(1)) if m2 else None
    series = re.sub(r"S\d+E\d+", " ", t, flags=re.I)
    series = re.sub(r"\bE\d+\b", " ", series, flags=re.I)
    series = re.sub(r"\bEpisode\s*\d+", " ", series, flags=re.I)
    series = re.sub(r"[\(\)\[\]]+", " ", series)                    # leftover empty parens
    series = re.sub(r"[\s\-–—:]+", " ", series).strip()
    if episode is None and " " in series and re.search(r"\d", series.split(" ")[0]):
        # e.g. "HentaiSanctuary01 - Hsin": the numbered head is the series.
        series = series.split(" ")[0].strip()
    if episode is None:
        # Nyaa trailing-number conventions:
        #   "Series 3 01" -> episode 3 (01 is a disc/part)
        #   "Series 05"  -> episode 5
        m2n = re.search(r"^(.*?)\s+(\d+)\s+(\d+)$", series)
        if m2n:
            episode = int(m2n.group(2))
            series = m2n.group(1).strip()
        else:
            m1n = re.search(r"^(.*?)\s+(\d+)$", series)
            if m1n:
                episode = int(m1n.group(2))
                series = m1n.group(1).strip()
    return series, episode


# Low-information tokens that must not drive a match on their own.
STOP_TOKENS = {"a", "an", "the", "is", "my", "vs", "of", "and", "to", "in",
                "on", "for", "no", "de", "la", "le", "du", "und",
                "english", "sub", "subs", "vostfr", "dub", "dual", "audio"}


def build_match_index(items):
    """Precompute per-item token sets + compact strings for fast matching."""
    index = []
    for it in items:
        text = (str(it.get("name") or "") + " " +
                 str(it.get("slug") or "") + " " +
                 str(it.get("search_titles") or "")).lower()
        tokens = set(re.sub(r"[^a-z0-9]+", " ", text).split())
        compact = re.sub(r"[^a-z0-9]+", "", text)
        index.append((it, tokens, compact))
    return index


def match_series(series, match_index):
    s = str(series or "").strip()
    if not s:
        return None, 0.0
    want = norm(s)
    # 1) Exact normalized name or slug.
    for it, _tokens, _compact in match_index:
        if norm(it.get("name")) == want or norm(it.get("slug")) == want:
            return it, 1.0
    # 2) Containment: every content token of our series must appear in the
    #    candidate. A lone stopword like "my" or a lone digit is not enough
    #    for a match on its own.
    tokens = [t for t in re.sub(r"[^a-z0-9]+", " ", s.lower()).split()
               if t and t not in STOP_TOKENS]
    if not tokens:
        return None, 0.0
    want_set = set(tokens)
    best, best_score = None, 0.0
    for it, cand_tokens, cand_compact in match_index:
        found = len(want_set & cand_tokens)
        score = found / len(want_set)
        # 3) Japanese/compact substring boost (e.g. 白書 / レモン白書).
        if len(want) >= 4 and want in cand_compact:
            score = max(score, 0.95)
        if score > best_score:
            best_score, best = score, it
    if best is not None and best_score >= 0.6:
        return best, best_score
    return None, best_score


def needs_full_name(name):
    n = str(name or "").strip()
    if not n:
        return True
    # Truncated scrape names: too short, a bare digit, or starting with a
    # lowercase letter (a mid-word fragment like "erflow 05" of "Overflow").
    if len(n) <= 8 or re.fullmatch(r"\d+", n) is not None:
        return True
    return n[0].isalpha() and n[0].islower()


def sukebei_view_name(nyaa_id):
    """Recover the full Sukebei title for rows whose name was truncated at
    scrape time (e.g. stored as a lone digit)."""
    html = http_get(f"{NYAA_VIEW_BASE}/{nyaa_id}")
    m = re.search(r"<h3[^>]*>(.*?)</h3>", html, re.S)
    if not m:
        return None
    return re.sub(r"<[^>]+>", "", m.group(1)).strip() or None


def clean_html(text):
    t = re.sub(r"<br\s*/?>", "\n", text or "")
    t = re.sub(r"</p>", "\n\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = html_mod.unescape(t)
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def merge_genres(existing, new):
    seen, out = set(), []
    for g in list(existing or []) + list(new or []):
        g = str(g or "").strip()
        if not g:
            continue
        key = g.lower()
        if key not in seen:
            seen.add(key)
            out.append(g)
    return out


def build_search_text(id_, name, genres):
    parts = [id_, name]
    parts.extend(genres or [])
    joined = " ".join(str(p) for p in parts if p).lower()
    hyphenless = joined.replace("-", "")
    compact = re.sub(r"[^a-z0-9\u3040-\u30ff\u4e00-\u9fff]+", "", joined)
    return f"{joined} {hyphenless} {compact}".strip()


def parse_runtime(html_text):
    m = re.search(r">\s*(\d{1,2}:\d{2}(?::\d{2})?)</span>", html_text)
    if not m:
        return ""
    return f"{int(m.group(1).split(':')[0])} min"


def hentaistream_lookup(series, episode, item, stream_cache, title_id):
    """Find the direct mp4 + runtime for one episode in a single page fetch."""
    if episode is None:
        return "", ""
    if title_id in stream_cache:
        return stream_cache[title_id], ""
    bases = []
    if item and item.get("slug"):
        bases.append(str(item["slug"]))
    bases.append(slugify(series))
    for base in bases:
        for pad in (f"{episode:02d}", str(episode)):
            url = f"{HENTAI_BASE}/{base}-episode-{pad}"
            try:
                page = http_get(url)
            except urllib.error.HTTPError as e:
                time.sleep(HVS_DELAY)
                if e.code == 404:
                    continue
                raise
            except OSError:
                # Transient socket timeout / connection failure: skip this
                # base/pad candidate and try the next one.
                time.sleep(HVS_DELAY)
                continue
            runtime = parse_runtime(page)
            time.sleep(HVS_DELAY)
            m = re.search(r'<iframe[^>]*src="([^"]*frames/[^"]+)"', page)
            if not m:
                continue
            frame_url = m.group(1)
            if not frame_url.startswith("http"):
                frame_url = HENTAI_BASE + frame_url
            try:
                frame = http_get(frame_url, referer=url)
            except urllib.error.HTTPError:
                time.sleep(FRAME_DELAY)
                continue
            except OSError:
                time.sleep(FRAME_DELAY)
                continue
            time.sleep(FRAME_DELAY)
            m2 = re.search(r'<source src="([^"]+\.mp4)"', frame, re.I)
            if m2:
                stream_cache[title_id] = m2.group(1)
                save_stream_cache(stream_cache)
                return m2.group(1), runtime
            if runtime:
                # Page exists but no mp4 (player may differ); keep the runtime.
                return "", runtime
    return "", ""


def main():
    ap = argparse.ArgumentParser(description="Enrich hnt:* rows in the live SQLite with hanime metadata + Hentaistream direct playback.")
    ap.add_argument("--limit", type=int, default=0, help="Process at most N rows (0 = all).")
    ap.add_argument("--no-play", action="store_true", help="Skip the Hentaistream direct-playback lookup.")
    ap.add_argument("--refresh", action="store_true", help="Refetch the hanime series list even if the cache exists.")
    args = ap.parse_args()

    items = load_hvs(refresh=args.refresh)
    source = "refetched" if args.refresh else ("cache" if os.path.exists(HVS_CACHE) else "fetched")
    print(f"hanime series loaded: {len(items)} (source: {source})")
    match_index = build_match_index(items)

    stream_cache = load_stream_cache()

    db = sqlite3.connect(DB_PATH)
    cur = db.cursor()
    cols = [r[1] for r in cur.execute("PRAGMA table_info(titles)").fetchall()]
    if "stream_url" not in cols:
        cur.execute("ALTER TABLE titles ADD COLUMN stream_url TEXT DEFAULT ''")
        db.commit()
        print("added titles.stream_url column")

    rows = cur.execute(
        "SELECT id, name, category, description, genres_json, poster, background, stream_url FROM titles WHERE id LIKE 'hnt:%' ORDER BY id"
    ).fetchall()
    if args.limit:
        rows = rows[:args.limit]
    print(f"hnt rows to process: {len(rows)}")

    matched = enriched = played = 0
    for row in rows:
        rid, name, category, desc, genres_raw, poster, background, stream_url = row
        name = html_mod.unescape(str(name or "").strip())
        existing_genres = json.loads(genres_raw or "[]")
        # Truncated names (e.g. a lone digit) can't drive a good match or a
        # direct-playback lookup; recover the full Sukebei title first.
        nyaa_id_m = re.match(r"hnt:nyaa-(\d+)", rid)
        if nyaa_id_m and needs_full_name(name):
            try:
                full = sukebei_view_name(nyaa_id_m.group(1))
            except urllib.error.HTTPError as e:
                full = None
                if e.code != 404:
                    raise
            except OSError:
                # Sukebei view fetch timed out or the connection dropped: skip
                # the recovery for this row and proceed to the next row.
                full = None
            time.sleep(VIEW_DELAY)
            if full and full != name:
                desc_lines = str(desc or "").split("\n")
                if desc_lines and desc_lines[0] == name:
                    desc_lines[0] = full
                cur.execute(
                    "UPDATE titles SET name = ?, description = ?, search_text = ? WHERE id = ?",
                    (
                        full,
                        "\n".join(desc_lines) if desc_lines else f"{full}\n\nSource: Nyaa/Sukebei ({category})",
                        build_search_text(rid, full, existing_genres[:2] or ["Hentai", "Censored"]),
                        rid,
                    ),
                )
                db.commit()
                name = full
                print(f"  {rid}: recovered full title: {full[:70]}")
        series, episode = parse_sukebei_name(name)
        item, score = match_series(series, match_index)

        if not item:
            # A row enriched by an earlier (looser) matcher may now be wrong.
            # If it carries a poster or a non-default description, reset it
            # to the thin Sukebei baseline.
            was_enriched = bool(poster) or (desc and "Source: Nyaa/Sukebei" not in desc)
            if was_enriched:
                base_genres = existing_genres[:2] or ["Hentai", "Censored"]
                cur.execute(
                    """UPDATE titles
                       SET description = ?, poster = '', background = '', genres_json = ?,
                           search_text = ?, stream_url = ?
                       WHERE id = ?""",
                    (
                        f"{name}\n\nSource: Nyaa/Sukebei ({category})",
                        json.dumps(base_genres),
                        build_search_text(rid, name, base_genres),
                        stream_url or "",
                        rid,
                    ),
                )
                db.commit()
                print(f"  {rid}: reset mis-enriched row (no match, score {score:.2f})")
            else:
                print(f"  {rid}: no hanime match for {series!r} (score {score:.2f})")
            continue

        matched += 1
        new_desc = clean_html(item.get("description"))
        if item.get("brand"):
            new_desc = (new_desc + "\n\nStudio: " + str(item["brand"]).strip()).strip()
        new_genres = merge_genres(existing_genres, item.get("tags") or [])
        new_search_text = build_search_text(rid, name, new_genres)

        play_url = stream_url or ""
        runtime = ""
        if not args.no_play and not play_url:
            play_url, runtime = hentaistream_lookup(series, episode, item, stream_cache, rid)
            if play_url:
                played += 1

        cur.execute(
            """UPDATE titles
               SET description = ?, poster = ?, background = ?, genres_json = ?,
                   search_text = ?, stream_url = ?, runtime = ?
               WHERE id = ?""",
            (
                new_desc,
                item.get("cover_url") or poster or "",
                item.get("poster_url") or background or "",
                json.dumps(new_genres),
                new_search_text,
                play_url or "",
                runtime,
                rid,
            ),
        )
        db.commit()
        enriched += 1
        if enriched % 25 == 0:
            print(f"  {enriched}/{len(rows)} enriched...")

    db.close()
    print(f"done: {enriched} enriched, {matched} matched, {played} direct-playback links found")


if __name__ == "__main__":
    main()

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
HS_CATALOG = os.path.join(ROOT, "addon", ".hentaistream_catalog.json")

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
HVS_URL = "https://guest.freeanimehentai.net/api/v11/search_hvs"
HENTAI_BASE = "https://tube.hentaistream.com"
# Alternate Hentaistream bases: the main site is the "different host"
# fallback when the tube subdomain misses.
HS_BASES = ["https://tube.hentaistream.com", "https://hentaistream.com"]
NYAA_VIEW_BASE = "https://sukebei.nyaa.si"

HVS_DELAY = 0.8   # seconds between hentaistream page fetches
FRAME_DELAY = 0.3  # seconds between frame fetches
VIEW_DELAY = 0.5   # seconds between sukebei view fetches
HS_MATCH_THRESH = 0.3  # min token-overlap score to accept a Hentaistream match


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


def load_hs_catalog():
    if os.path.exists(HS_CATALOG):
        try:
            with open(HS_CATALOG, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def build_hs_index(catalog):
    """Precompute per-entry token sets + compact title, plus an
    episode->entry-index map and a token->entry-index posting list, so a
    lookup scans only the entries for the target episode instead of the
    whole catalog (avoids a full 3782-entry scan per row).

    Returns a 3-tuple: (entries, ep_map, postings).
    """
    entries = []
    ep_map = {}
    postings = {}
    for c in catalog:
        title = str(c.get("title") or c.get("slug") or "").lower()
        tokens = set(t for t in re.sub(r"[^a-z0-9]+", " ", title).split() if t)
        compact = re.sub(r"[^a-z0-9]+", "", title)
        i = len(entries)
        entries.append((c, tokens, compact))
        for t in tokens:
            postings.setdefault(t, []).append(i)
        if c.get("mp4") and c.get("episode") is not None:
            # The catalog stores episode as a string ("1"); normalize to int
            # so the lookup (which uses an int episode) matches these keys.
            try:
                ep_val = int(str(c["episode"]).strip())
            except ValueError:
                ep_val = None
            if ep_val is not None:
                ep_map.setdefault(ep_val, []).append(i)
    return (entries, ep_map, postings)


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


def hentaistream_lookup(series, episode, item, stream_cache, title_id, hs_index=None):
    """Find the direct mp4 + runtime for one episode.

    Prefers the Hentaistream catalog (matched by series title + episode#);
    falls back to slug-guessing when the catalog is absent or unmatched.
    """
    # OVAs / single-file torrents have no episode number; Hentaistream
    # indexes them as episode 1, so look them up under episode 1.
    ep = episode if episode is not None else 1
    if title_id in stream_cache:
        return stream_cache[title_id], ""
    # 1) Catalog lookup: match series title, pick the entry for this episode
    #    that carries a direct mp4.
    if hs_index:
        entries, ep_map, postings = hs_index
        s = str(series or "").strip()
        want_tokens = [t for t in re.sub(r"[^a-z0-9]+", " ", s.lower()).split() if t and t not in STOP_TOKENS]
        want_set = set(want_tokens)
        want_compact = norm(s)
        best, best_score = None, 0.0
        if ep in ep_map:
            # Candidate set: the smallest token posting (or the episode's
            # mp4 entries if the series name has no content tokens).
            cand = None
            if want_set:
                cand = min((postings.get(t, []) for t in want_set), key=len, default=[])
            if cand is None:
                cand = ep_map[ep]
            for i in cand:
                c, cand_tokens, cand_compact = entries[i]
                if not c.get("mp4"):
                    continue
                found = len(want_set & cand_tokens)
                score = found / len(want_set) if want_set else 0.0
                if len(want_compact) >= 4 and want_compact in cand_compact:
                    score = max(score, 0.9)
                if score > best_score:
                    best_score, best = score, c
        if best is not None and best_score >= HS_MATCH_THRESH:
            stream_cache[title_id] = best["mp4"]
            save_stream_cache(stream_cache)
            return best["mp4"], best.get("runtime", "")
        # 1b) Series-level fallback: the episode-specific match can miss when
        #     the requested episode isn't in the catalog. Scan the whole
        #     catalog (all episodes) via the token postings and take the best
        #     matching entry that carries a direct mp4.
        want2 = [t for t in re.sub(r"[^a-z0-9]+", " ", s.lower()).split() if t and t not in STOP_TOKENS]
        want_set2 = set(want2)
        best2, best_score2 = None, 0.0
        if want_set2:
            cand2 = min((postings.get(t, []) for t in want_set2), key=len, default=[])
            for i in cand2:
                c, cand_tokens, cand_compact = entries[i]
                if not c.get("mp4"):
                    continue
                score2 = len(want_set2 & set(cand_tokens)) / len(want_set2)
                if len(want_compact) >= 4 and want_compact in cand_compact:
                    score2 = max(score2, 0.9)
                if score2 > best_score2:
                    best_score2, best2 = score2, c
        if best2 is not None and best_score2 >= HS_MATCH_THRESH:
            stream_cache[title_id] = best2["mp4"]
            save_stream_cache(stream_cache)
            return best2["mp4"], best2.get("runtime", "")
    # 2) Fallback: slug-guess the episode page and dig out the player frame.
    #    Try BOTH Hentaistream bases (tube + main) — the main site is the
    #    "different host" fallback when the tube subdomain misses.
    bases = []
    if item and item.get("slug"):
        bases.append(str(item["slug"]))
    bases.append(slugify(series))
    for hs_base in HS_BASES:
        for base in bases:
            candidates = [f"{hs_base}/{base}-episode-{ep:02d}",
                          f"{hs_base}/{base}-episode-{ep}"]
            if episode is None:
                candidates.append(f"{hs_base}/{base}")  # bare slug: single-file OVA
            for url in candidates:
                try:
                    page = http_get(url)
                except urllib.error.HTTPError as e:
                    time.sleep(HVS_DELAY)
                    # 404 = no such episode; 5xx = transient server error —
                    # skip this candidate and try the next, don't kill the run.
                    if e.code == 404 or 500 <= e.code < 600:
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
                    frame_url = hs_base + frame_url
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


# ---------------------------------------------------------------------------
# Second direct-mp4 source: a different host than Hentaistream-tube.
# Tries the Hentaistream MAIN site (hentaistream.com) and, if the page has
# no mp4, a different anime-hentai video host.
# ---------------------------------------------------------------------------
SECOND_PLAYBACK_BASES = [
    "https://hentaistream.com",
]


def second_playback_lookup(series, episode, item, stream_cache, title_id, hs_index=None):
    """Second mp4 source: different host. Returns (url, runtime) or ("", "")."""
    if title_id in stream_cache:
        return stream_cache[title_id], ""
    ep = episode if episode is not None else 1
    s = str(series or "").strip()
    bases = []
    if item and item.get("slug"):
        bases.append(str(item["slug"]))
    if s:
        bases.append(slugify(s))
    for base_host in SECOND_PLAYBACK_BASES:
        for base in bases:
            candidates = [f"{base_host}/{base}-episode-{ep:02d}",
                          f"{base_host}/{base}-episode-{ep}"]
            if episode is None:
                candidates.append(f"{base_host}/{base}")
            for url in candidates:
                try:
                    page = http_get(url)
                except urllib.error.HTTPError as e:
                    time.sleep(HVS_DELAY)
                    # 404 = no such episode; 5xx = transient — skip and continue.
                    if e.code == 404 or 500 <= e.code < 600:
                        continue
                    raise
                except OSError:
                    time.sleep(HVS_DELAY)
                    continue
                runtime = parse_runtime(page)
                time.sleep(HVS_DELAY)
                m = re.search(r'<iframe[^>]*src="([^"]*frames/[^"]+)"', page)
                if not m:
                    continue
                frame_url = m.group(1)
                if not frame_url.startswith("http"):
                    frame_url = base_host + frame_url
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
    return "", ""


# ---------------------------------------------------------------------------
# Second direct-playback source: xanimeporn.com. WordPress site that hosts
# episode pages under /<slug>-episode-N/. The player (fluidPlayer) leaves
# <source src=""/> empty (JS-populated), but the mp4 path
# (wp-content/uploads/.../<name>.mp4) is referenced in the page's JSON-LD/JS.
# ---------------------------------------------------------------------------
XANIME_BASE = "https://xanimeporn.com"


def url_ok(url):
    """HEAD-check that a playback URL actually resolves (skip moved/404 files)."""
    class _H(urllib.request.Request):
        def get_method(self):
            return "HEAD"
    req = _H(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return True
    except urllib.error.HTTPError as e:
        return e.code == 200
    except OSError:
        return False


def xanimeporn_lookup(series, episode, item, stream_cache, title_id):
    """Second mp4 source (xanimeporn.com): slug-guess the episode page and
    extract the direct mp4. Only return a URL that actually resolves."""
    if title_id in stream_cache:
        return stream_cache[title_id], ""
    ep = episode if episode is not None else 1
    s = str(series or "").strip()
    bases = []
    if item and item.get("slug"):
        bases.append(str(item["slug"]))
    if s:
        bases.append(slugify(s))
    for base in bases:
        for url in (f"{XANIME_BASE}/{base}-episode-{ep:02d}/",
                     f"{XANIME_BASE}/{base}-episode-{ep}/"):
            try:
                page = http_get(url)
            except urllib.error.HTTPError as e:
                time.sleep(HVS_DELAY)
                # 404 = no such episode; 5xx = transient — skip and continue.
                if e.code == 404 or 500 <= e.code < 600:
                    continue
                raise
            except OSError:
                time.sleep(HVS_DELAY)
                continue
            time.sleep(HVS_DELAY)
            # The mp4 path is referenced in the page's JSON-LD / JS strings.
            m = re.search(r'(https?://xanimeporn\.com/wp-content/uploads/[^"\'\s\\]+?\.mp4)', page)
            mp4 = m.group(1) if m else ""
            if mp4 and url_ok(mp4):
                stream_cache[title_id] = mp4
                save_stream_cache(stream_cache)
                return mp4, ""
    return "", ""


# ---------------------------------------------------------------------------
# Second poster source: a different cover host for rows whose hanime match
# had no cover_url. Fetches a poster image from a different site.
# ---------------------------------------------------------------------------
SECOND_POSTER_BASE = "https://hentaistream.com"


def poster_fallback(series, item):
    """Return a poster URL from a second source, or "" if none.
    Looks for a <img> cover on the second source's series/episode page."""
    ep = (item or {}).get("episode")
    slug = (item or {}).get("slug")
    if not slug:
        slug = slugify(series) if series else ""
    if not slug:
        return ""
    url = f"{SECOND_POSTER_BASE}/{slug}"
    try:
        page = http_get(url)
    except (urllib.error.HTTPError, OSError):
        return ""
    m = re.search(r'<img[^>]*class="[^"]*(?:cover|poster|thumb)[^"]*"[^>]*src="([^"]+)"', page, re.I)
    if not m:
        m = re.search(r'<meta[^>]*property="og:image"[^>]*content="([^"]+)"', page, re.I)
    return m.group(1) if m else ""


# Third poster source: the Sukebei torrent view page carries an oceanveil
# vertical title poster (image.oceanveil.net/public/anime_titles/.../N_...jpg)
# or an og:image. Keyed by nyaa id, so it works for every hnt:nyaa-* row.
SUKUBEI_VIEW_BASE = "https://sukebei.nyaa.si"


def sukebei_view_poster(nyaa_id):
    """Fetch the Sukebei view page for a nyaa id and return a poster URL.

    Prefers the oceanveil vertical title poster; falls back to og:image,
    skipping the generic default avatar (not a real poster)."""
    page = http_get(f"{SUKUBEI_VIEW_BASE}/view/{nyaa_id}")
    m = re.search(r'https://image\.oceanveil\.net/public/anime_titles/[^"\'\s]+', page, re.I)
    if m:
        return m.group(0)
    m2 = re.search(r'<meta[^>]*property="og:image"[^>]*content="([^"]+)"', page, re.I)
    if m2:
        url = m2.group(1)
        # The Sukebei view page's og:image is often the generic avatar; skip it.
        if "default.png" not in url and "avatar" not in url:
            return url
    return ""


# ---------------------------------------------------------------------------
# Fourth poster source: hstream.moe hosts per-episode covers at
# /images/hentai/<slug>/cover-ep-<N>.webp. Slug-guess from the series name
# and HEAD-check the cover; a different host from the others.
# ---------------------------------------------------------------------------
HSTREAM_BASE = "https://hstream.moe"


def hstream_poster(series, episode):
    """Return an hstream.moe cover URL for one episode, or "" if none.

    Slugs on hstream.moe are romanized series names, so slugifying the hnt
    series name is the natural candidate; we HEAD-check before returning."""
    ep = episode if episode is not None else 1
    slug = slugify(series) if series else ""
    if not slug:
        return ""
    url = f"{HSTREAM_BASE}/images/hentai/{slug}/cover-ep-{ep}.webp"
    try:
        if url_ok(url):
            time.sleep(VIEW_DELAY)
            return url
    except OSError:
        return ""
    return ""


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

    hs_catalog = load_hs_catalog()
    hs_index = build_hs_index(hs_catalog)
    print(f"hentaistream catalog entries: {len(hs_catalog)}")

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

        # Direct playback is attempted for EVERY hnt row (OVA or not),
        # independent of whether hanime matched.
        play_url = stream_url or ""
        runtime = ""
        if not args.no_play and not play_url:
            play_url, runtime = hentaistream_lookup(series, episode, item, stream_cache, rid, hs_index)
            if not play_url:
                # Second mp4 source (a different host).
                su, sr = second_playback_lookup(series, episode, item, stream_cache, rid, hs_index)
                if su:
                    play_url = su
                    runtime = runtime or sr
            if not play_url:
                # Third mp4 source (xanimeporn.com, a different site).
                su, sr = xanimeporn_lookup(series, episode, item, stream_cache, rid)
                if su:
                    play_url = su
                    runtime = runtime or sr
            if play_url:
                played += 1

        # Third poster source: Sukebei view page (oceanveil poster), keyed by
        # nyaa id — works for every hnt:nyaa-* row.
        view_poster = ""
        if nyaa_id_m and not (poster or "").strip():
            try:
                view_poster = sukebei_view_poster(nyaa_id_m.group(1))
                time.sleep(VIEW_DELAY)
            except (urllib.error.HTTPError, OSError):
                view_poster = ""
        # Fourth poster source (hstream.moe cover) when the Sukebei view
        # poster is absent and the series name can be slugified.
        hstream_p = ""
        if not view_poster and series:
            hstream_p = hstream_poster(series, episode)

        if not item:
            # A row enriched by an earlier (looser) matcher may now be wrong.
            # If it carries a poster or a non-default description, reset it
            # to the thin Sukebei baseline.
            poster_val = view_poster or hstream_p or ""
            was_enriched = bool(poster) or (desc and "Source: Nyaa/Sukebei" not in desc)
            if was_enriched:
                base_genres = existing_genres[:2] or ["Hentai", "Censored"]
                cur.execute(
                    """UPDATE titles
                       SET description = ?, poster = ?, background = '', genres_json = ?,
                           search_text = ?, stream_url = ?
                       WHERE id = ?""",
                    (
                        f"{name}\n\nSource: Nyaa/Sukebei ({category})",
                        poster_val,
                        json.dumps(base_genres),
                        build_search_text(rid, name, base_genres),
                        play_url or "",
                        rid,
                    ),
                )
                db.commit()
                print(f"  {rid}: reset mis-enriched row (no match, score {score:.2f})")
            else:
                # Preserve a fetched Sukebei-view poster AND the direct-playback
                # URL on a non-matched row. Without this, a found mp4 (already
                # saved to the JSON cache) was discarded because the row had no
                # poster and a default description.
                if poster_val or play_url:
                    cur.execute(
                        "UPDATE titles SET poster = ?, stream_url = ?, runtime = ? WHERE id = ?",
                        (poster_val, play_url or "", runtime or "", rid),
                    )
                    db.commit()
                print(f"  {rid}: no hanime match for {series!r} (score {score:.2f})")
            continue

        matched += 1
        new_desc = clean_html(item.get("description"))
        if item.get("brand"):
            new_desc = (new_desc + "\n\nStudio: " + str(item["brand"]).strip()).strip()
        new_genres = merge_genres(existing_genres, item.get("tags") or [])
        new_search_text = build_search_text(rid, name, new_genres)
        new_poster = item.get("cover_url") or view_poster or poster or ""
        if not new_poster:
            # Second poster source (different host) for hanime rows without a cover.
            new_poster = poster_fallback(series, item)
        if not new_poster:
            # Fourth poster source (different host): hstream.moe cover.
            new_poster = hstream_poster(series, episode)

        cur.execute(
            """UPDATE titles
               SET description = ?, poster = ?, background = ?, genres_json = ?,
                   search_text = ?, stream_url = ?, runtime = ?
               WHERE id = ?""",
            (
                new_desc,
                new_poster,
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

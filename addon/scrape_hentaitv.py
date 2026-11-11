#!/usr/bin/env python3
"""Scrape hentai.tv into the live SQLite (single 'hentai' category).

hentai.tv is the new source for the hentai catalog (replaces the two old
hnt series categories). Layout:
  - series list:  https://hentai.tv/series?page=N  (40 pages)
  - series page:  https://hentai.tv/series/<slug>  (title, description,
    per-episode poster-img tags; episode 1's cover is the series poster)
  - episode page: https://hentai.tv/hentai/<slug>-episode-N
    (schema.org VideoObject in the Next.js RSC payload: name, description,
    thumbnailUrl, uploadDate, duration, genre, embedUrl)
  - player page:  https://nhplayer.com/v/<id>/  (direct mp4 + srt, base64
    in the server button's data-id)

Usage:
  python3 addon/scrape_hentaitv.py --series sister-breeder   # one series (example)
  python3 addon/scrape_hentaitv.py --all                        # whole site
"""
import argparse
import base64
import json
import re
import sqlite3
import time
import urllib.error
import urllib.request

ROOT = __file__.rsplit("/addon/", 1)[0]
SQLITE = f"{ROOT}/debrid-app/data/titles.sqlite"
BASE = "https://hentai.tv"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
DELAY = 0.8

_db = None


def db():
    global _db
    if _db is None:
        _db = sqlite3.connect(SQLITE)
    return _db


def get(url, delay=True):
    if delay:
        time.sleep(DELAY)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "ignore")


def b64url(v):
    pad = "=" * (-len(v) % 4)
    return base64.b64decode(v + pad).decode()


def series_slugs_page(page):
    html = get(f"{BASE}/series?page={page}")
    return sorted(set(re.findall(r'href="/series/([^"/]+)"', html)))


def parse_series_page(html):
    title = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    desc = re.search(r'name="description" content="([^"]*)"', html)
    posters = re.findall(r'<img class="poster-img"[^>]+src="([^"]+)"', html)
    n_eps = len(posters)
    poster = "https://hentai.tv" + posters[0] if posters else ""
    # episode count also stated in the description: "6 episodes"
    m = re.search(r"(\d+)\s+episodes", desc.group(1) if desc else "")
    if m:
        n_eps = int(m.group(1))
    return {
        "title": title.group(1).strip() if title else "",
        "description": desc.group(1) if desc else "",
        "poster": poster,
        "episodes": n_eps,
    }


def extract_video_object(html):
    i = html.find('"@type":"VideoObject"')
    if i < 0:
        return None
    start = html.rfind("{", 0, i)
    depth, j = 0, start
    while j < len(html):
        c = html[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    try:
        return json.loads(html[start:j + 1])
    except json.JSONDecodeError:
        return None


def parse_info_grid(html):
    """Parse the episode page's info-grid: Brand / Quality / Rating / Year / Language / Release date."""
    out = {}
    for k, v in re.findall(r'<div class="info-k">(.*?)</div><div class="info-v[^"]*">(.*?)</div>', html, re.S):
        v = re.sub(r'<svg.*?</svg>', '', v, flags=re.S)
        v = re.sub(r'<[^>]+>', '', v).strip()
        out[k] = v
    return out


def parse_tag_row(html):
    return re.findall(r'<a class="tag-chip" href="/genre/[^"]+">([^<]+)</a>', html)


def build_release_info(info):
    parts = []
    for k in ("Brand", "Quality", "Rating", "Year", "Language", "Release date"):
        v = info.get(k)
        if v:
            label = "Released" if k == "Release date" else k
            parts.append(f"{label}: {v}")
    return " • ".join(parts)


def parse_episode_page(html):
    obj = extract_video_object(html)
    if not obj:
        return {}
    def first(v):
        if isinstance(v, list):
            return v[0] if v else ""
        return v or ""
    duration = first(obj.get("duration"))
    name = obj.get("name", "")
    # "Watch Sister Breeder Episode 1 Online at Hentai.tv®" -> "Sister Breeder Episode 1"
    m = re.match(r"Watch (.+?) Online at", name)
    clean_name = m.group(1) if m else name
    # Some episode pages title as "... - Watch on AnimeIDHentai" -> strip the suffix.
    clean_name = re.sub(r"\s*-\s*Watch on .*$", "", clean_name)
    thumb = first(obj.get("thumbnailUrl"))
    if thumb and not thumb.startswith("http"):
        thumb = "https://hentai.tv" + thumb
    info = parse_info_grid(html)
    tags = parse_tag_row(html)
    if not tags:
        tags = obj.get("genre") or []
    return {
        "name": clean_name,
        "description": first(obj.get("description", "")).replace("➨", "").replace("☝", "").strip(),
        "thumbnail": thumb,
        "released": first(obj.get("uploadDate", "")),
        "duration": iso_to_hms(duration),
        "genres": tags,
        "brand": info.get("Brand", ""),
        "quality": info.get("Quality", ""),
        "rating": info.get("Rating", ""),
        "year": info.get("Year", ""),
        "language": info.get("Language", ""),
        "release_date": info.get("Release date", ""),
        "release_info": build_release_info(info),
        "embed_url": first(obj.get("embedUrl", "")),
        "url": first(obj.get("url", "")),
    }


def iso_to_hms(isodur):
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?([0-9]+)S", isodur or "")
    if not m:
        return ""
    h = int(m.group(1) or 0)
    mi = int(m.group(2) or 0)
    s = int(m.group(3) or 0)
    if h:
        return f"{h}:{mi:02d}:{s:02d}"
    return f"{mi}:{s:02d}"


def nhplayer_urls(embed_url):
    """Decode the base64 video/subtitle urls from the nhplayer page."""
    if not embed_url:
        return "", ""
    html = get(embed_url, delay=True)
    m = re.search(r'data-id="([^"]+)"', html)
    if not m:
        return "", ""
    params = dict(re.findall(r"([a-z]+)=([^&\"]+)", m.group(1)))
    mp4 = b64url(params["vid"]).split("|")[0] if "vid" in params else ""
    srt = b64url(params["s"]).replace("%20", " ") if "s" in params else ""
    return mp4, srt


def upsert_series(slug, series_info, episodes):
    d = db()
    n = len(episodes)
    # Series row: poster = episode-1 cover, description/released/release_info/rating
    # all come from episode 1's page (user instruction).
    ep1 = next((e for e in episodes if e["num"] == 1), episodes[0] if episodes else None)
    released = ep1["released"] if ep1 else ""
    release_info = ep1.get("release_info", "") if ep1 else ""
    rating = ep1.get("rating", "") if ep1 else ""
    description = ep1["description"] if ep1 else series_info["description"]
    genres = sorted({g for e in episodes for g in e["genres"]})
    d.execute(
        """
        INSERT INTO titles (id, category, name, description, poster, background, released, runtime, series_key, episode_num, genres_json, release_info, imdb_rating, posted_at, search_text)
        VALUES (?, 'hentai', ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          category = 'hentai', name = excluded.name,
          description = excluded.description, poster = excluded.poster,
          background = excluded.background, released = excluded.released,
          genres_json = excluded.genres_json,
          release_info = excluded.release_info,
          imdb_rating = excluded.imdb_rating,
          search_text = excluded.search_text
        """,
        (
            f"hnt:{slug}",
            series_info["title"] or slug,
            description,
            series_info["poster"],
            series_info["poster"],
            released,
            "",
            slug,
            json.dumps(genres, ensure_ascii=False),
            release_info,
            rating,
            time.strftime("%Y-%m-%d"),
            f"hentai {slug} {series_info['title']} {json.dumps(genres, ensure_ascii=False)}",
        ),
    )
    for ep in episodes:
        d.execute(
            """
            INSERT INTO titles (id, category, name, description, poster, background, released, runtime, series_key, episode_num, stream_url, release_info, imdb_rating, genres_json, posted_at, search_text)
            VALUES (?, 'hentai', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              category = 'hentai', name = excluded.name,
              description = excluded.description, poster = excluded.poster,
              background = excluded.background, released = excluded.released,
              runtime = excluded.runtime, stream_url = excluded.stream_url,
              release_info = excluded.release_info,
              imdb_rating = excluded.imdb_rating,
              genres_json = excluded.genres_json,
              search_text = excluded.search_text
            """,
            (
                f"hnt:{slug}:{ep['num']}",
                ep["name"],
                ep["description"],
                ep["thumbnail"],
                ep["thumbnail"],
                ep["released"],
                ep["duration"],
                slug,
                ep["num"],
                ep.get("mp4", ""),
                ep.get("release_info", ""),
                ep.get("rating", ""),
                json.dumps(ep.get("genres", []), ensure_ascii=False),
                time.strftime("%Y-%m-%d"),
                f"hentai {slug} {ep['name']} {ep['description'][:80]}",
            ),
        )
    d.commit()


def scrape_series(slug):
    html = get(f"{BASE}/series/{slug}")
    info = parse_series_page(html)
    episodes = []
    for n in range(1, info["episodes"] + 1):
        ehtml = get(f"{BASE}/hentai/{slug}-episode-{n}")
        ep = parse_episode_page(ehtml)
        ep["num"] = n
        mp4, _srt = nhplayer_urls(ep.get("embed_url", ""))
        ep["mp4"] = mp4
        episodes.append(ep)
        print(f"  ep{n}: {ep['name']} | {ep['duration']} | mp4: {mp4 or 'NONE'}")
    upsert_series(slug, info, episodes)
    print(f"[hentaitv] {slug}: {info['episodes']} episodes -> category 'hentai'")
    return {"slug": slug, "series": info, "episodes": episodes}


def scrape_all():
    seen = set()
    slugs = []
    page = 1
    while True:
        batch = series_slugs_page(page)
        fresh = [s for s in batch if s not in seen]
        seen.update(batch)
        slugs.extend(fresh)
        if not fresh or page >= 40:
            break
        page += 1
        time.sleep(DELAY)
    for i, slug in enumerate(slugs):
        try:
            scrape_series(slug)
        except Exception as e:
            print(f"[hentaitv] {slug}: FAILED ({type(e).__name__} {e})")
    print(f"[hentaitv] done: {len(slugs)} series scraped")
    return slugs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", help="scrape a single series slug (example mode)")
    ap.add_argument("--all", action="store_true", help="scrape the whole site")
    ap.add_argument("--limit", type=int, default=0, help="max series for --all (0 = all)")
    args = ap.parse_args()
    if args.series:
        scrape_series(args.series)
    elif args.all:
        slugs = scrape_all()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()

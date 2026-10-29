# JAVSTREAMIO

Scrapes sehuatang.org (Discuz forum behind an 18+ gate) and streams the
records through pluggable output plugins.

## Data model

Each record: `url`, `tag` (无码高清/有码高清), `titleno`, `title`, `出演女优`,
`影片容量`, `是否有码`, `posted_at`, `preview_images`, `magnet`.

## scrape.py

```bash
python3 scrape.py <url> [--pages N] [--details] [--out FILE] [--batch N] [--delay F]
```

- `--details`: opens every thread on each listed page and extracts the movie
  fields, preview images and magnet links.
- Output is appended to JSONL with url-based resume (already-saved threads
  are skipped).

## streamio plugin package

```bash
python3 -m streamio --list                 # list plugins
```

Built-in plugins (add your own by subclassing `streamio.core.Plugin` and
decorating it with `@register`):

| plugin | output | purpose |
|--------|--------|---------|
| `jsonl`  | `streamio.jsonl` | pass-through of the record stream |
| `csv`    | `catalog.csv`  | tabular export (utf-8-sig, Excel-ready) |
| `rss`    | `feed.xml`     | RSS 2.0 syndication feed |
| `magnet` | `magnets.txt`  | one magnet link per line |

### Consume an existing data file

```bash
python3 -m streamio --input data/forum-103-pages-1-2.jsonl --plugin csv
```

### Full pipeline: scrape then stream

```bash
python3 -m streamio --scrape https://sehuatang.org/forum-103-1.html --pages 1 --plugin jsonl
```

### Push magnets to a torrent client

```bash
python3 -m streamio --input data/forum-103-pages-1-2.jsonl --plugin magnet \
  --push --torrent-base http://localhost:8080 [--torrent-user U --torrent-pass P]
# or for Transmission:
python3 -m streamio --input data/forum-103-pages-1-2.jsonl --plugin magnet \
  --push --torrent-client transmission --torrent-base http://localhost:9091
```

Environment variables `QBIT_URL`, `QBIT_USER`, `QBIT_PASS` override the defaults.

## Stremio addon

The addon is a set of static JSON files (manifest, catalog, per-title meta and
streams) committed to `addon/` and served by GitHub raw. Add this link to
Stremio's addon field:

```
https://raw.githubusercontent.com/borepig/javstreamio/main/addon/manifest.json
```

### Building the addon

```bash
python3 addon/build_addon.py              # fetch full details from JavDB
python3 addon/build_addon.py --no-javdb   # offline build (no JavDB fetches)
```

- `addon/build_addon.py` generates `addon/manifest.json`, the catalog, and
  per-title `meta`/`stream` files from `data/*.jsonl`.
- **Meta enrichment:** by default it queries JavDB (`javdb.com`) by title
  number to add full cast, genres, runtime, release date, maker/series/rating.
  A local cache (`addon/.javdb_cache.json`) avoids re-fetching; the build is
  rate-limit aware (honors `Retry-After` on HTTP 429, browser-like headers to
  avoid 403 bot-blocks). Re-run the build (with the cache) to fill in any
  numbers that were rate-limited on a previous run.
- **Debrid streaming:** each `stream` object carries the torrent `infoHash`.
  A Stremio client paired with an installed Real-Debrid (or any BitTorrent
  engine) resolves the hash to a playable stream at play time — no API key or
  server-side resolution needed.

After editing `data/*.jsonl` or the build script, rebuild, then `git add
addon/ && git commit && git push` to refresh the live addon.

## Installing the wheel

```bash
pip install dist/javstreamio-0.1.0-py3-none-any.whl
```

The repo is public; release `v0.1.0` ships the wheel.

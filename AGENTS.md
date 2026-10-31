# JAVSTREAMIO agent notes

## Stremio `/meta` response shape (do not regress)

Stremio Web "No metadata was found!" / "No videos found for this meta!" after a
catalog click means `/meta/{type}/{id}.json` did not deserialize as
`ResourceResponse.Meta`. Catalog can still work.

**Correct** (what Cinemeta and stremio-core expect):

```json
{ "meta": { "id": "jav:WAAA-629", "type": "movie", "name": "..." } }
```

**Wrong** (both fail; both were shipped):

```json
{ "id": "jav:WAAA-629", "type": "movie", "name": "..." }
{ "meta": [ { "id": "jav:WAAA-629", "type": "movie" } ] }
```

- Catalog: `{ "metas": [ ... ] }` (array).
- Meta: `{ "meta": { ... } }` (**object**, not array).
- Stream: `{ "streams": [ ... ] }` (array).
- Do not copy `protocol.md`'s `/meta` example that wraps `meta` in an array.
  Ground truth is a live Cinemeta response, e.g.
  `https://v3-cinemeta.strem.io/meta/movie/tt1254207.json`.
- Movies should set `meta.behaviorHints.defaultVideoId` to the same id so
  Stremio Web does not fall back to Season 0 / Episode 1.
- `released` MUST be RFC3339 (`2026-05-06T00:00:00.000Z`), not a date-only
  string. stremio-core parses it as `DateTime`; `2026-05-06` fails and the
  client still shows "No metadata was found!" even when `{ "meta": ... }`
  wrapping is correct. Catalog items omit `released`, which is why the
  board works and the detail page does not.
- Builder: `addon/build_addon.py` `as_meta_resource()`.
- Proxy: `debrid-app/lib/addon-data.js` `normalizeMetaBody()`.
- Regression: `python3 tests/test_meta_protocol.py`.
- After a protocol change, bump `addon/manifest.json` version so Stremio refetches.
- Poster URLs from `tu.djhdhs.us` are Cloudflare-hotlink-protected (403 without
  `Referer: https://sehuatang.org/`). Do not send those to Stremio. Use public
  DMM covers instead (`dmm_poster()` / `dmmPoster()`): catalog `poster` is
  `{code}ps.jpg`, detail `background` is `{code}pl.jpg`. JavDB covers on
  `jdbstatic.com` are the fallback when there is no DMM code. The Vercel
  proxy rewrites leftover forum posters to those public hosts so Stremio
  fetches images directly and the Vercel site does not store or host covers.
  Catalogs are `jav-censored`, `jav-uncensored`, `western`, and `others`.
  JAV `/meta` descriptions come from JavDB cache (cast, genres, maker, runtime,
  rating) plus jav321 official titles and the forum blurb. Do not apply JavDB
  rows whose 番號 does not match the title number.
- `/stream` with `?service=torbox&token=` must POST the magnet to Torbox
  `createtorrent` (multipart), keep `torrent_id` on `DUPLICATE_ITEM` / `mylist`,
  and return a `/play?service=torbox&token=&torrent_id=&file_id=` URL. `/play`
  GETs `requestdl` **without** `redirect`/`zip_link`, then **302**s to the CDN
  file URL. Do not give Stremio `requestdl?redirect=true` (307) or a zip — the
  player will not start the video. `/play` HEAD must not resolve Torbox (200
  video/mp4 only) or the second title 502s before GET. `bingeGroup` is per
  infoHash. On `ACTIVE_LIMIT`, pause a seeding/active torrent and retry create.
- Real-Debrid uses `https://api.real-debrid.com/rest/1.0` with form-urlencoded
  `addMagnet` → `selectFiles` → `unrestrict/link`, then the `download` field
  (not `link`). `/play` 302s that URL. Instant-availability is gone; do not call it.
- AllDebrid: `POST /v4/magnet/upload` (`magnets[]`) → `/v4.1/magnet/status` until
  Ready → `/v4/magnet/files` → `/v4/link/unlock`. Bearer token, not `/v4/magnet`.
- Premiumize: `/play` calls `POST /api/transfer/directdl?apikey=&src=magnet`.
- Debrid-Link: `POST /api/v2/seedbox/add` then `/seedbox/list` `downloadUrl`.
- Stream rows: `name` is the left badge (`Torbox\n5.19G`), `description` is the
  right detail. Torrent fallback MUST be `{ infoHash }` — never `{ url: "magnet:..." }`.
  Stremio Web cannot play magnets; stremio-core drops non-http `url` values.

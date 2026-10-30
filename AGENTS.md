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
- Builder: `addon/build_addon.py` `as_meta_resource()`.
- Proxy: `debrid-app/lib/addon-data.js` `normalizeMetaBody()`.
- Regression: `python3 tests/test_meta_protocol.py`.
- After a protocol change, bump `addon/manifest.json` version so Stremio refetches.

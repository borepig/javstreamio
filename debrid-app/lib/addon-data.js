// Serves the static Stremio addon files (manifest, catalog, meta, stream) by proxying them
// from the GitHub raw URL of the javstreamio repo. Each title's own stream file carries its
// magnet, so the app never loads the whole catalog at once.

const RAW = "https://raw.githubusercontent.com/borepig/javstreamio/main/addon";
const cache = new Map(); // url -> { body, ts }
const TTL = 60_000; // 60s: bounds the in-memory cache so new addon versions propagate

export function rawUrl(relPath) {
  // Append a per-request cache-buster so the GitHub raw (CloudFront) CDN can't serve a
  // stale cached copy — forces a fresh origin fetch of the latest addon files.
  return `${RAW}/${relPath}?nc=${Date.now()}`;
}

// Stremio's ResourceResponse.Meta is `{ meta: <object> }`. The static GitHub files were
// historically written as a bare Meta Object (or `{ meta: [object] }`); either shape
// makes stremio-core drop the response and the client shows "No metadata was found!".
function rfc3339Released(value) {
  if (!value || typeof value !== "string") return null;
  const text = value.trim();
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(text)) {
    return /Z|[+-]\d{2}:\d{2}$/.test(text) ? text : `${text}Z`;
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return `${text}T00:00:00.000Z`;
  return null;
}

export function normalizeMetaBody(body) {
  if (!body || typeof body !== "object" || Array.isArray(body)) return body;
  const inner = body.meta;
  let meta;
  if (inner && typeof inner === "object" && !Array.isArray(inner)) {
    meta = inner;
  } else if (Array.isArray(inner) && inner[0] && typeof inner[0] === "object") {
    meta = inner[0];
  } else if (body.id && body.type) {
    meta = body;
  } else {
    return body;
  }
  const released = rfc3339Released(meta.released);
  if (released) meta.released = released;
  else delete meta.released;
  if (meta.id) {
    const hints = { ...(meta.behaviorHints || {}) };
    if (!hints.defaultVideoId) hints.defaultVideoId = meta.id;
    if (hints.hasScheduledVideos == null) hints.hasScheduledVideos = false;
    meta.behaviorHints = hints;
  }
  return { meta };
}

export async function fetchAddon(relPath) {
  // Stremio requests file paths without an extension (e.g. "meta/movie/jav:WAAA-629"), but the
  // addon files on GitHub raw are stored with a ".json" extension. Append it when missing so the
  // proxied fetch hits the real file instead of 404.
  const fileRel = relPath.endsWith(".json") ? relPath : relPath + ".json";
  const url = rawUrl(fileRel);
  // The manifest is always fetched fresh so a version bump propagates immediately.
  const isManifest = relPath === "manifest.json";
  if (!isManifest) {
    const hit = cache.get(relPath);
    if (hit && Date.now() - hit.ts < TTL) return hit.body;
  }
  const res = await fetch(url);
  if (!res.ok) {
    const err = new Error(`Failed to fetch ${fileRel}: HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  const body = await res.json();
  cache.set(relPath, { body, ts: Date.now() });
  return body;
}

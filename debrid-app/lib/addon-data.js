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

// Serves the static Stremio addon files (manifest, catalog, meta, stream) by proxying them
// from the GitHub raw URL of the javstreamio repo. Each title's own stream file carries its
// magnet, so the app never loads the whole catalog at once.

const RAW = "https://raw.githubusercontent.com/borepig/javstreamio/main/addon";
const cache = new Map();

export function rawUrl(relPath) {
  return `${RAW}/${relPath}`;
}

export async function fetchAddon(relPath) {
  const url = rawUrl(relPath);
  const hit = cache.get(url);
  if (hit) return hit;
  const res = await fetch(url);
  if (!res.ok) {
    const err = new Error(`Failed to fetch ${relPath}: HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  const body = await res.json();
  cache.set(url, body);
  return body;
}

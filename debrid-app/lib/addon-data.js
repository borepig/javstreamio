// Serves the static Stremio addon files (manifest, catalog, meta, static stream) by
// proxying them from the GitHub raw URL of the javstreamio repo. The /stream endpoint is
// dynamic and resolves the infoHash through the user's debrid service.

const RAW = "https://raw.githubusercontent.com/borepig/javstreamio/main/addon";
const DATA_RAW = "https://raw.githubusercontent.com/borepig/javstreamio/main/data/forum-103-all.jsonl";
const cache = new Map();
const THREAD_RE = /thread-(\d+)/;

export function rawUrl(relPath) {
  return `${RAW}/${relPath}`;
}

export async function fetchAddon(relPath) {
  const url = rawUrl(relPath);
  const hit = cache.get(url);
  if (hit) return hit;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to fetch ${relPath}: HTTP ${res.status}`);
  const body = await res.json();
  cache.set(url, body);
  return body;
}

// metaId -> magnet index built from the scraped JSONL, so the dynamic /stream can add
// torrents that aren't cached on the debrid.
let magnetIndex = null;

function metaIdFromRecord(rec) {
  const titleno = (rec.titleno || "").trim();
  if (titleno) return "jav:" + titleno;
  const m = THREAD_RE.exec(rec.url || "");
  return m ? "jav:thread-" + m[1] : "jav:unknown";
}

export async function loadMagnetIndex() {
  if (magnetIndex) return magnetIndex;
  const res = await fetch(DATA_RAW);
  if (!res.ok) throw new Error("Failed to fetch data JSONL: HTTP " + res.status);
  const text = await res.text();
  const idx = new Map();
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    let rec;
    try { rec = JSON.parse(line); } catch { continue; }
    const id = metaIdFromRecord(rec);
    if (rec.magnet) idx.set(id, rec.magnet);
  }
  magnetIndex = idx;
  return idx;
}

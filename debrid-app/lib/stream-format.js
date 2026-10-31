// Stremio keeps the first /stream body for cacheMaxAge seconds. A first miss
// ("not cached") then sticks, so later Real-Debrid hits never show Cached.
export const STREAM_CACHE_HINTS = {
  cacheMaxAge: 0,
  staleRevalidate: 0,
  staleError: 0,
};

// Stremio stream rows. `name` is the left-hand badge (source + size);
// `description` is the right-hand detail (title + flags). Torrent fallbacks
// must use `infoHash`, not `url: magnet:...` — Stremio Web cannot play magnets
// and stremio-core drops non-http `url` values.

export function splitSizeFlags(raw) {
  const text = String(raw || "").trim();
  if (!text) return { size: "", flags: "" };
  const m = text.match(/^(\d+(?:\.\d+)?\s*(?:[KMGT]B|[KMGT]iB|[GT]))(?:\s+|$)(.*)$/i);
  if (!m) return { size: "", flags: text };
  return { size: m[1].replace(/\s+/g, ""), flags: (m[2] || "").trim() };
}

export function pickTorrentId(body) {
  if (body == null) return null;
  const data = body.data !== undefined ? body.data : body;
  if (data == null) return null;
  if (typeof data === "number" || typeof data === "string") {
    const n = data;
    if (n === "" || n === 0 || n === "0") return null;
    return n;
  }
  const id = data.torrent_id ?? data.torrentId ?? data.id;
  if (id === 0 || id === "0" || id == null || id === "") return null;
  if (Array.isArray(data.torrents) && data.torrents[0]) {
    return data.torrents[0].id ?? data.torrents[0].torrent_id ?? id;
  }
  return id;
}

export function torrentRows(data) {
  if (!data) return [];
  if (Array.isArray(data)) return data;
  if (Array.isArray(data.torrents)) return data.torrents;
  if (data.id != null || data.hash || data.info_hash) return [data];
  return [];
}

export function unwrapTorrent(body, wantId) {
  const data = body && body.data;
  if (!data) return null;
  const rows = torrentRows(data);
  if (wantId != null && wantId !== "") {
    const hit = rows.find(
      (t) => String(t.id) === String(wantId) || String(t.torrent_id) === String(wantId),
    );
    if (hit) return hit;
  }
  if (!Array.isArray(data) && !Array.isArray(data.torrents)) return data;
  return rows[0] || null;
}

export function parseCachedLookup(body, hash) {
  const want = String(hash || "").toLowerCase();
  if (!want) return false;
  const data = body && (body.data !== undefined ? body.data : body.response !== undefined ? body.response : body);
  if (data == null) return false;
  if (data === true) return true;
  if (Array.isArray(data)) {
    if (data.length && data.every((item) => item === true || item === false)) return data[0] === true;
    return data.some((item) => {
      if (item === true) return true;
      if (!item || typeof item !== "object") {
        return String(item || "").toLowerCase() === want;
      }
      const key = String(item.hash || item.info_hash || item.id || "").toLowerCase();
      return key === want || item.cached === true || item.instant === true;
    });
  }
  if (typeof data === "object") {
    const hit = data[want] || data[hash] || data[String(hash || "").toUpperCase()];
    if (hit === true) return true;
    if (hit && typeof hit === "object") return hit.cached !== false;
    if (data.cached === true || data.instant === true) return true;
  }
  return false;
}

export function matchHash(row, hash) {
  if (!row || !hash) return false;
  const h = String(hash).toLowerCase();
  const candidates = [row.hash, row.info_hash, row.infoHash, row.active_hash];
  return candidates.some((c) => c && String(c).toLowerCase() === h);
}

const IDLE_JOB = /paused|inactive/;
const DOWNLOADING_JOB = /download|metadl|queued|process|stalled|magnetiz|checking|allocat/;

export function isSlotBlockingJob(row) {
  if (!row) return false;
  const state = String(row.download_state || row.status || "").toLowerCase();
  if (IDLE_JOB.test(state)) return false;
  if (row.active === true) return true;
  if (DOWNLOADING_JOB.test(state)) return true;
  if (/upload|seed/.test(state) && !row.cached) return true;
  return false;
}

export function otherSlotBlockingJobs(rows, keepHash) {
  return (Array.isArray(rows) ? rows : []).filter((row) => {
    if (keepHash && matchHash(row, keepHash)) return false;
    return isSlotBlockingJob(row);
  });
}

export function slotLimitForPlan(plan, extra = 0) {
  const base = { 0: 1, 1: 3, 3: 5, 2: 10 }[Number(plan)];
  return (base == null ? 1 : base) + (Number(extra) || 0);
}

export function jobsToStop(rows, keepHash, limit) {
  const others = otherSlotBlockingJobs(rows, keepHash);
  if (limit == null || !Number.isFinite(Number(limit))) return others;
  const need = Math.max(0, others.length - Math.max(0, Number(limit) - 1));
  return others.slice(0, need);
}

export function pickVideoFile(files) {
  if (!Array.isArray(files) || !files.length) return null;
  const video = /\.(mp4|mkv|avi|mov|wmv|ts|m4v|iso|m2ts)$/i;
  const fileName = (f) => f.name || f.short_name || f.path || "";
  const named = files.filter((f) => video.test(fileName(f)));
  const pool = named.length ? named : files;
  return pool.slice().sort((a, b) => (b.size || b.bytes || 0) - (a.size || a.bytes || 0))[0];
}

export function flattenAdFiles(nodes, out = []) {
  if (!Array.isArray(nodes)) return out;
  for (const n of nodes) {
    if (!n) continue;
    if (Array.isArray(n.e)) flattenAdFiles(n.e, out);
    else if (n.l) out.push({ name: n.n || "", path: n.n || "", size: n.s || 0, link: n.l });
  }
  return out;
}

export function serviceLabel(service) {
  const key = String(service || "").toLowerCase().replace(/[\s-]+/g, "");
  if (key.includes("torbox")) return "Torbox";
  if (key.includes("realdebrid") || key.includes("real")) return "Real-Debrid";
  if (key.includes("alldebrid")) return "AllDebrid";
  if (key.includes("premiumize") || key.includes("pretorx")) return "Premiumize";
  if (key.includes("debridlink")) return "Debrid-Link";
  return service || "Debrid";
}

export function includeDebridRow(service, cached) {
  if (cached) return true;
  const key = String(service || "").toLowerCase().replace(/[\s-]+/g, "");
  // Real-Debrid removed instant-availability. Still return /play so addMagnet
  // runs when Stremio opens the stream, same as Torbox.
  return key.includes("torbox") || key.includes("realdebrid");
}

export function parseAdInstant(body, hash) {
  const want = String(hash || "").toLowerCase();
  if (!want) return false;
  const magnets = body && body.data && body.data.magnets;
  const rows = Array.isArray(magnets) ? magnets : magnets ? [magnets] : [];
  return rows.some((row) => {
    const key = String((row && (row.hash || row.magnet || row.id)) || "").toLowerCase();
    return (key === want || key.includes(want)) && (row.instant === true || row.ready === true);
  });
}

export function hasFileId(fileId) {
  return fileId != null && fileId !== "";
}

// Stremio plays this URL, which 302s to a fresh Torbox CDN file. Do not hand
// Stremio `requestdl?redirect=true` (307 + JSON/zip) — the player will not start.
export function playStreamUrl(origin, { service, token, torrentId, fileId, infoHash, cached, titleCode }) {
  const q = new URLSearchParams({
    service: service || "torbox",
    token: token || "",
  });
  if (torrentId != null && torrentId !== "") q.set("torrent_id", String(torrentId));
  if (hasFileId(fileId)) q.set("file_id", String(fileId));
  if (infoHash) q.set("hash", String(infoHash));
  if (titleCode) q.set("code", String(titleCode));
  if (cached) q.set("cached", "1");
  return `${origin}/play?${q.toString()}`;
}

const DEFAULT_TRACKERS = [
  "udp://tracker.opentrackr.org:1337/announce",
  "udp://open.stealth.si:80/announce",
];

export function magnetWithTrackers(magnet, hash) {
  let link = String(magnet || "").trim();
  if (!link && hash) link = `magnet:?xt=urn:btih:${hash}`;
  if (!link) return "";
  if (link.includes("&tr=") || link.includes("?tr=")) return link;
  return `${link}${DEFAULT_TRACKERS.map((tr) => `&tr=${tr}`).join("")}`;
}

export function buildAddonStreams({
  origin,
  service,
  token,
  infoHash,
  torrentId,
  titleCode,
  sourceName,
  sourceDescription,
  cached = false,
  includeDebrid = true,
  resolvedUrl = "",
}) {
  const debridUrl =
    resolvedUrl ||
    (includeDebrid && service && token && infoHash
      ? playStreamUrl(origin, { service, token, infoHash, torrentId, cached, titleCode })
      : "");
  return buildStreamList({
    service,
    debridUrl,
    infoHash,
    sourceName,
    sourceDescription,
    cached,
    token,
    setupUrl: origin,
  });
}

export function formatDebridStream({ service, url, size, title, flags, infoHash, cached }) {
  const label = cached ? `${serviceLabel(service)} (Cached)` : serviceLabel(service);
  const sizeBit = size || "";
  const group = infoHash || title || "x";
  return {
    name: sizeBit ? `${label}\n${sizeBit}` : label,
    description: [title, flags].filter(Boolean).join("\n"),
    url,
    behaviorHints: { bingeGroup: `javstreamio-${label}-${group}` },
  };
}

export function formatTorrentStream({ infoHash, size, title, flags }) {
  const sizeBit = size || "";
  const hash = String(infoHash || "").toLowerCase();
  return {
    name: sizeBit ? `P2P\n${sizeBit}` : "P2P",
    description: [title, flags || "Magnet"].filter(Boolean).join("\n"),
    infoHash: hash,
    behaviorHints: {
      bingeGroup: `javstreamio-p2p-${hash || title || "x"}`,
      notWebReady: true,
    },
  };
}

export function formatSetupStream(origin) {
  return {
    name: "Setup",
    description:
      "Paste your debrid API key at javstremio.vercel.app and reinstall that addon URL. Play stays black without it.",
    externalUrl: origin || "https://javstremio.vercel.app",
  };
}

export function buildStreamList({
  service,
  debridUrl,
  infoHash,
  sourceName,
  sourceDescription,
  cached,
  token,
  setupUrl,
}) {
  const { size, flags } = splitSizeFlags(sourceName);
  const title = sourceDescription || "";
  const out = [];
  if (!token && setupUrl) {
    out.push(formatSetupStream(setupUrl));
  }
  if (debridUrl) {
    out.push(formatDebridStream({ service, url: debridUrl, size, title, flags, infoHash, cached }));
  }
  if (infoHash) {
    out.push(formatTorrentStream({ infoHash, size, title, flags }));
  }
  return out;
}

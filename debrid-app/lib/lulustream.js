// Lulustream / underhentai live m3u8 proxy.
//
// The m3u8 is session-bound (needs a CDN clearance cookie) and time-limited
// (~8h). The debrid-app resolves it at request time and proxies the m3u8 +
// HLS segments so Stremio's player plays without holding the CDN session.
//
// The CDN session cookie (cf_clearance) is provided via the LULU_COOKIE env
// var, mirroring the JAVDB_COOKIE pattern. Without it the m3u8/segment
// fetches 403.

const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36";
const LULU_COOKIE = process.env.LULU_COOKIE || "";
const LULU_BASE = "https://luluvdo.com";
const CACHE_TTL_MS = 6 * 60 * 60 * 1000; // 6h (m3u8 is valid ~8h)

// In-memory cache of parsed m3u8 parts per token. The CDN host + path are
// stable per video; only the signed query (t, s) regenerates per load.
const partsCache = new Map(); // token -> { parts, at }

// Parse the pipe-delimited parts string and build the m3u8 URL.
//
// The pipe string is regenerated on every fetch (the signed t/s query values
// change per load), but the m3u8 block's layout is stable relative to the
// `hls2` anchor:
//   P[i-5]=m3u8, P[i-4]=master, P[i-3]=<vid>_h, P[i-2]=<dir2>, P[i-1]=<dir1>
//   P[i-6]=sig, P[i+1]=tnmr, P[i+3]=cdnNum (when present).
// Directory values vary per video (e.g. "02"/"03338" vs "04"/"03229"), and the
// CDN number is only sometimes present, so we resolve the block positionally
// and fall back to LULU_CDN_NUM (default "cdn1015") when the cdn part is absent.
function parsePipeParts(html) {
  const m = html.match(/'([^']*hls2[^']*)'\s*\.split\('\|'\)/);
  if (!m) return null;
  const P = m[1].split("|");
  const i = P.indexOf("hls2");
  if (i < 6) return null;
  const m3u8 = P[i - 5];
  const master = P[i - 4];
  const vidDir = P[i - 3];
  const dir2 = P[i - 2];
  const dir1 = P[i - 1];
  const sig = P[i - 6] || "";
  const tnmr = P[i + 1] || "tnmr";
  const cdnNum = P.find((v) => /^cdn\d+$/.test(v)) || process.env.LULU_CDN_NUM || "cdn1015";
  const ts = P.find((v) => /^\d{10}$/.test(v)) || "";
  const fid = P.find((v) => /^\d{8}$/.test(v)) || "";
  const eExp = P.find((v) => v === "28800") || "28800";
  if (!m3u8 || !master || !vidDir || !dir2 || !dir1) return null;
  const host = "https://" + cdnNum + ".cdn-" + tnmr + ".org";
  const path = "/hls2/" + dir1 + "/" + dir2 + "/" + vidDir + "/" + master + "." + m3u8;
  const query = "?t=" + sig + "&s=" + ts + "&e=" + eExp + "&f=" + fid + "&i=0.3&sp=0";
  return { url: host + path + query, host };
}

async function getLuluHeaders(token) {
  const h = { "User-Agent": UA, "Referer": "https://www.underhentai.net/" };
  if (LULU_COOKIE) h["Cookie"] = LULU_COOKIE;
  return h;
}

// Fetch the luluvdo page, parse the pipe parts, and build the m3u8 URL.
// Caches the parsed parts (host + path are stable) for up to 6h.
export async function buildLuluM3u8(token) {
  const hit = partsCache.get(token);
  if (hit && Date.now() - hit.at < CACHE_TTL_MS) return hit.parts;
  const res = await fetch(LULU_BASE + "/e/" + token, { headers: await getLuluHeaders(token) });
  if (!res.ok) throw new Error("luluvdo fetch failed: " + res.status);
  const parts = parsePipeParts(await res.text());
  if (!parts) throw new Error("could not parse pipe parts for " + token);
  partsCache.set(token, { parts, at: Date.now() });
  return parts; // { url, host }
}

export function rewritePlaylist(body, proxyBase) {
  return body.split("\n").map((line) => {
    const t = line.trim();
    if (!t || t[0] === "#" || t.startsWith("http://") || t.startsWith("https://")) return line;
    return proxyBase + "/seg/" + t;
  }).join("\n");
}

// Proxy the master m3u8: fetch it (with CDN session cookie), rewrite every
// relative playlist/segment reference to the proxy route.
export async function proxyM3u8(token) {
  const { url } = await buildLuluM3u8(token);
  const res = await fetch(url, { headers: await getLuluHeaders(token) });
  if (!res.ok) throw new Error("m3u8 fetch failed: " + res.status);
  const body = rewritePlaylist(await res.text(), "/lulu/" + token);
  return body;
}

// Proxy a CDN resource (sub-playlist or segment) referenced by the rewritten
// m3u8. Returns { kind, res } where kind is "playlist" or "segment".
export async function proxySegment(token, segPath) {
  const { host } = await buildLuluM3u8(token);
  const res = await fetch(host + "/" + segPath, { headers: await getLuluHeaders(token) });
  if (!res.ok) throw new Error("segment fetch failed: " + res.status);
  const isPlaylist = segPath.endsWith(".m3u8");
  return { isPlaylist, res };
}

export { LULU_COOKIE };

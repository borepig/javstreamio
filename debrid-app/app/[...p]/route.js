import { NextResponse } from "next/server";
import {
  fetchAddon,
  fetchRemotePoster,
  fourhoiPoster,
  needsJacketCrop,
  jacketArtSources,
  normalizeMetaBody,
  withPublicPosters,
  withBundledCatalogs,
  bundledCatalog,
  bundledMeta,
  bundledStream,
  bundledTitle,
  resolveCatalogId,
  parseCatalogExtra,
} from "../../lib/addon-data.js";
import { isForumPlaceholder, jpegSize, resolveJacketImage } from "../../lib/jacket-poster.js";
import { createHash } from "crypto";
import { mkdir, readFile, writeFile } from "fs/promises";
import os from "os";
import path from "path";
import { lookupDebridCache, resolveDebrid, resolvePlay, unlockRdLinks, rdShouldServeCachingClip } from "../../lib/debrids.js";
import { buildAddonStreams, includeDebridRow, STREAM_CACHE_HINTS } from "../../lib/stream-format.js";
import { proxyM3u8, proxySegment, rewritePlaylist } from "../../lib/lulustream.js";
import { ADDON_ID, ADDON_NAME, ADDON_VERSION } from "../../lib/version.js";
import { isValidPaidKeyAsync, registerDevice } from "../../lib/paid-keys.js";
import {
  FREE_CATALOG_SIZE,
  PAID_ONLY_CATEGORIES,
  isInFreeSet,
  mapTitleRow,
  openTitlesDb,
  queryEpisodeByRef,
  querySeriesMeta,
  queryTitle,
  resolveSqlitePath,
} from "../../lib/titles-db.js";

export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";
export const revalidate = 0;
export const maxDuration = 10;

// Addon files change when the JAVSTREAMIO repo is updated (new metas, version bumps),
// so tell the Vercel edge (and any intermediary) not to cache the proxied responses.
// "no-cache, no-store, must-revalidate" defeats both the Vercel edge cache and any
// intermediary HTTP cache, so a manifest version bump propagates immediately.
const NO_STORE = { "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0, s-maxage=0" };

// One shared read-only handle for the live titles DB (free-tier meta gate).
let routeDb;
function getRouteDb() {
  if (routeDb !== undefined) return routeDb;
  const file = resolveSqlitePath();
  routeDb = file ? openTitlesDb(file) : null;
  return routeDb;
}

// Generated SVG title card for cat3 rows whose stored forum-preview host
// 404/403s (uploadhouse.com / pic2607v.cc go stale when the forum moves
// files around). Served in place of a 502 so the board keeps a visible
// (ugly-but-present) poster until the poster-fill job stores a real one.
function chunkLongWord(word, maxChars) {
  if (word.length <= maxChars) return [word];
  const chunks = [];
  for (let i = 0; i < word.length; i += maxChars) chunks.push(word.slice(i, i + maxChars));
  return chunks;
}

function wrapCardText(text, maxChars) {
  const out = [];
  let cur = "";
  for (const word of String(text || "").split(/\s+/)) {
    for (const chunk of chunkLongWord(word, maxChars)) {
      if ((cur + " " + chunk).trim().length > maxChars && cur) {
        out.push(cur);
        cur = chunk;
      } else {
        cur = (cur + " " + chunk).trim();
      }
    }
  }
  if (cur) out.push(cur);
  return out.slice(0, 6);
}

function svgTitleCard(name) {
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const lines = wrapCardText(name, 16);
  const startY = 270 - ((lines.length - 1) * 36) / 2;
  const tspans = lines
    .map((l, i) => `    <tspan x="180" ${i ? 'dy="36"' : `y="${startY}"`}>${esc(l)}</tspan>`)
    .join("\n");
  return `<svg xmlns="http://www.w3.org/2000/svg" width="360" height="540" viewBox="0 0 360 540">
  <rect width="360" height="540" fill="#1c1e26"/>
  <rect x="8" y="8" width="344" height="524" fill="none" stroke="#3d4254" stroke-width="2"/>
  <text x="180" fill="#e8eaf2" font-family="Arial, sans-serif" font-size="26" font-weight="bold" text-anchor="middle" dominant-baseline="middle">
${tspans}
  </text>
  <text x="180" y="500" fill="#8a90a8" font-family="Arial, sans-serif" font-size="14" text-anchor="middle">JAV STREAMIO</text>
</svg>`;
}

function clientIp(req) {
  const forwarded = req.headers.get("x-forwarded-for") || "";
  return forwarded.split(",")[0].trim() || req.headers.get("x-real-ip") || "";
}

// The Web Request's `url.origin` reflects the listener address (localhost:3000)
// rather than the public host behind nginx, so /play and /lulu URLs end up
// unreachable from Stremio on the user's machine. Derive the public origin from
// the proxied headers (X-Forwarded-Proto + Host) with `url.origin` as fallback.
function publicOrigin(req, url) {
  const proto = req.headers.get("x-forwarded-proto") || "http";
  const host = req.headers.get("x-forwarded-host") || req.headers.get("host") || url.host;
  return `${proto}://${host}`;
}

function withTimeout(promise, ms, fallback) {
  return Promise.race([
    promise,
    new Promise((resolve) => setTimeout(() => resolve(fallback), ms)),
  ]);
}

// This Node build's URL searchParams / Buffer utf8 / TextDecoder /
// decodeURIComponent corrupt specific CJK 3-byte sequences (e.g. 單體作品
// decodes as "??體??品"), so the genre filter can't match rows. Workaround:
// parse the raw percent-encoded query, percent-decode to bytes, then decode
// UTF-8 bytes to code points in pure JS (String.fromCodePoint is clean).
function decodeRawQueryParam(name, url) {
  const raw = String(url.search || "").replace(/^\?/, "");
  if (!raw) return "";
  for (const pair of raw.split("&")) {
    const eq = pair.indexOf("=");
    if (eq > 0 && pair.slice(0, eq) === name) {
      const value = pair.slice(eq + 1);
      const bytes = [];
      for (let i = 0; i < value.length; i++) {
        if (
          value[i] === "%" &&
          i + 2 < value.length &&
          /^%[0-9A-Fa-f]{2}$/.test(value.slice(i, i + 3))
        ) {
          bytes.push(parseInt(value.slice(i + 1, i + 3), 16));
          i += 2;
        } else {
          const code = value.charCodeAt(i);
          if (code <= 0x7f) {
            bytes.push(code);
          } else if (code <= 0x7ff) {
            bytes.push(0xc0 | (code >> 6), 0x80 | (code & 0x3f));
          } else {
            const next = i + 1 < value.length ? value.charCodeAt(i + 1) : 0;
            if (code >= 0xd800 && code <= 0xdbff && next >= 0xdc00 && next <= 0xdfff) {
              const cp = 0x10000 + ((code - 0xd800) << 10) + (next - 0xdc00);
              bytes.push(0xf0 | (cp >> 18), 0x80 | ((cp >> 12) & 0x3f), 0x80 | ((cp >> 6) & 0x3f), 0x80 | (cp & 0x3f));
              i += 1;
            } else {
              bytes.push(0xf0 | (code >> 18), 0x80 | ((code >> 12) & 0x3f), 0x80 | ((code >> 6) & 0x3f), 0x80 | (code & 0x3f));
            }
          }
        }
      }
      let out = "";
      let i = 0;
      while (i < bytes.length) {
        const b = bytes[i];
        if (b < 0x80) {
          out += String.fromCharCode(b);
          i += 1;
        } else if (b >= 0xf0 && i + 3 < bytes.length) {
          const cp = ((b & 0x07) << 18) | ((bytes[i + 1] & 0x3f) << 12) | ((bytes[i + 2] & 0x3f) << 6) | (bytes[i + 3] & 0x3f);
          out += String.fromCodePoint(cp);
          i += 4;
        } else if (b >= 0xe0 && i + 2 < bytes.length) {
          const cp = ((b & 0x0f) << 12) | ((bytes[i + 1] & 0x3f) << 6) | (bytes[i + 2] & 0x3f);
          out += String.fromCodePoint(cp);
          i += 3;
        } else if (b >= 0xc0 && i + 1 < bytes.length) {
          const cp = ((b & 0x1f) << 6) | (bytes[i + 1] & 0x3f);
          out += String.fromCodePoint(cp);
          i += 2;
        } else {
          i += 1;
        }
      }
      return out;
    }
  }
  return "";
}

const JACKET_CACHE = path.join(os.tmpdir(), "javstreamio-jacket");
const HK_FORUM_CACHE = path.join(os.tmpdir(), "javstreamio-hkforum");

function imageResponse(body, type) {
  return new NextResponse(body, {
    headers: {
      "Content-Type": type || "image/jpeg",
      "Cache-Control": "public, max-age=86400, s-maxage=86400",
      "Access-Control-Allow-Origin": "*",
      "Cross-Origin-Resource-Policy": "cross-origin",
    },
  });
}

function sniffImageType(buf) {
  if (buf && buf.length > 8 && buf[0] === 0x89 && buf[1] === 0x50) return "image/png";
  if (buf && buf.length > 3 && buf[0] === 0xff && buf[1] === 0xd8) return "image/jpeg";
  if (buf && buf.length > 12 && buf.toString("ascii", 0, 4) === "RIFF") return "image/webp";
  return "";
}

// Generated HK Cat III cards live in public/posters/hkcat3. Read them off
// disk: fetching our own /posters URL from inside the request deadlocks.
async function readOwnPoster(url) {
  let parsed;
  try {
    parsed = new URL(String(url || ""));
  } catch {
    return null;
  }
  if (parsed.hostname !== "javstremio.ddns.net") return null;
  const match = parsed.pathname.match(/^\/posters\/([a-z0-9-]+)\/([^/]+)$/i);
  if (!match) return null;
  try {
    return await readFile(path.join(process.cwd(), "public", "posters", match[1], match[2]));
  } catch {
    return null;
  }
}

// Sehuatang attachment, cached. A dead host or the 1024xp QR card is a miss
// so the caller can serve the generated poster instead.
async function cachedForumPoster(url) {
  const key = createHash("sha1").update(String(url)).digest("hex").slice(0, 20);
  const hit = path.join(HK_FORUM_CACHE, `${key}.img`);
  const miss = path.join(HK_FORUM_CACHE, `${key}.miss`);
  try {
    return await readFile(hit);
  } catch {
    /* fetch below */
  }
  try {
    await readFile(miss);
    return null;
  } catch {
    /* not a known miss */
  }
  const rememberMiss = async () => {
    await mkdir(HK_FORUM_CACHE, { recursive: true });
    await writeFile(miss, "").catch(() => {});
  };
  let buf;
  try {
    const img = await fetchRemotePoster(url, true);
    const type = img.headers.get("content-type") || "";
    if (!type.startsWith("image/")) {
      await rememberMiss();
      return null;
    }
    buf = Buffer.from(await img.arrayBuffer());
  } catch {
    await rememberMiss();
    return null;
  }
  const kind = sniffImageType(buf);
  if (!kind) {
    await rememberMiss();
    return null;
  }
  const dim = kind === "image/jpeg" ? jpegSize(buf) : null;
  if (dim && isForumPlaceholder(buf.length, dim.width, dim.height)) {
    await rememberMiss();
    return null;
  }
  await mkdir(HK_FORUM_CACHE, { recursive: true });
  await writeFile(hit, buf).catch(() => {});
  return buf;
}

async function jacketPosterResponse(id, item, fanart) {
  const safe = String(id).replace(/[^A-Za-z0-9._-]+/g, "_");
  const file = path.join(JACKET_CACHE, `${safe}.${fanart ? "fanart" : "poster"}.v4.jpg`);
  let body = null;
  try {
    body = await readFile(file);
  } catch {
    body = null;
  }
  if (!body) {
    const sources = jacketArtSources(item || { id });
    body = await resolveJacketImage(
      sources,
      async (src) => {
        const img = await fetchRemotePoster(src);
        return Buffer.from(await img.arrayBuffer());
      },
      { fanart },
    );
    if (body) {
      await mkdir(JACKET_CACHE, { recursive: true });
      await writeFile(file, body).catch(() => {});
    }
  }
  if (!body) return null;
  return new NextResponse(body, {
    headers: {
      "Content-Type": "image/jpeg",
      "Cache-Control": "public, max-age=86400, s-maxage=86400",
      "Access-Control-Allow-Origin": "*",
      "Cross-Origin-Resource-Policy": "cross-origin",
    },
  });
}

// Catch-all serving the Stremio addon protocol. `app/page.js` still owns "/", so this
// handles /manifest.json, /catalog/..., /meta/... (proxied from GitHub raw) and a dynamic
// /stream/... that resolves the torrent through the user's debrid service + key.
export async function GET(req, { params }) {
  const p = params.p || [];
  const seg0 = p[0];
  const url = new URL(req.url);
  const service = url.searchParams.get("service") || "";
  const token = url.searchParams.get("token") || "";
  const paidKey = url.searchParams.get("key") || "";
  const paid = await isValidPaidKeyAsync(paidKey);
  const device = url.searchParams.get("device") || "";
  if (paid && device) {
    const dev = registerDevice(paidKey, device);
    if (!dev.allowed) {
      return NextResponse.json(
        { error: `Device limit reached (${dev.count}/${dev.limit})` },
        { status: 403 },
      );
    }
  }

  try {
    // /manifest.json is a single-segment path whose value includes the ".json" suffix.
    if (p.length === 1 && p[0] === "manifest.json") {
      let m;
      try {
        m = await fetchAddon("manifest.json");
      } catch {
        m = { id: ADDON_ID, name: ADDON_NAME, version: ADDON_VERSION, resources: [], types: ["movie"] };
      }
      return NextResponse.json(withBundledCatalogs(m), { headers: NO_STORE });
    }
    if (seg0 === "catalog") {
      // Keep the raw catalog id: year catalogs ("jav-censored-year") are
      // views whose selected year arrives via the `genre` param (Cinemeta
      // pattern); bundledCatalog uses the raw id to apply the year filter.
      const rawCatalogId = String(p[2] || "jav-censored").replace(/\.json$/, "");
      const catalogId = resolveCatalogId(rawCatalogId);
      const extra = String(p[3] || "").replace(/\.json$/, "");
      const {
        skip: pathSkip,
        search: pathSearch,
        genre: pathGenre,
        year: pathYear,
      } = parseCatalogExtra(extra);
      // Stremio sends extras as URL query params (standard protocol);
      // also honor the legacy path-based extra.
      // 0.5.66: query values are decoded manually (raw percent-decode +
      // pure-JS UTF-8) because this Node build's built-in query decoding
      // corrupts CJK genre strings like 單體作品.
      const qSkip = Number(decodeRawQueryParam("skip", url) || 0);
      const qSearch = decodeRawQueryParam("search", url);
      const qGenre = decodeRawQueryParam("genre", url).trim();
      const qYear = decodeRawQueryParam("year", url).trim();
      const skip = Math.max(pathSkip, qSkip);
      const search = qSearch || pathSearch;
      const genre = qGenre || pathGenre;
      const year = qYear || pathYear;
      // Free tier: newest FREE_CATALOG_SIZE per catalog. Paid tier: full catalog.
      // Paid-only categories (HK AV, hentai, Korean, Chinese) return an empty
      // catalog for free users.
      if (!paid && PAID_ONLY_CATEGORIES.includes(catalogId)) {
        return NextResponse.json({ metas: [] }, { headers: NO_STORE });
      }
      const catalog = bundledCatalog(rawCatalogId, skip, paid ? undefined : FREE_CATALOG_SIZE, search, genre, year);
      const body = withPublicPosters(catalog, "catalog", publicOrigin(req, url));
      return NextResponse.json(body, { headers: NO_STORE });
    }
    if (seg0 === "meta") {
      const rel = p.join("/");
      const id = p[2] || "";
      let body = bundledMeta(id);
      if (!body) {
        try {
          body = await fetchAddon(rel);
        } catch (e) {
          if (e.status === 404) return NextResponse.json({ error: "Not found" }, { status: 404 });
          throw e;
        }
      }
      // Free tier gets its own meta view: only the newest FREE_CATALOG_SIZE
      // titles per category, so free meta can't leak paid titles.
      if (!paid) {
        const db = getRouteDb();
        if (!db || !isInFreeSet(db, id)) {
          return NextResponse.json({ error: "Not found" }, { status: 404 });
        }
      }
      body = withPublicPosters(normalizeMetaBody(body), "meta", publicOrigin(req, url));
      return NextResponse.json(body, { headers: NO_STORE });
    }
    if (seg0 === "poster") {
      const type = p[1] || "movie";
      const id = decodeURIComponent((p[2] || "").replace(/\.(jpe?g|png|webp)$/i, ""));
      if (!id) return NextResponse.json({ error: "Not found" }, { status: 404 });
      const fanart = url.searchParams.get("fanart") === "1";
      // hnt refs are series/episode ids, not row ids — resolve them through
      // the titles DB before falling back to the bundled/static lookup.
      let item = null;
      const db = getRouteDb();
      if (db && /^hnt:[^:]+:\d+$/.test(id)) {
        const row = queryEpisodeByRef(db, id);
        if (row) item = mapTitleRow(row);
      } else if (db && /^hnt:[^:]+$/.test(id)) {
        const series = querySeriesMeta(db, id);
        if (series && series.poster) item = { id, poster: series.poster };
      } else if (db && /^hk:/.test(id)) {
        // HK Cat III rows (douban poster or live-host SVG card).
        item = queryTitle(db, id);
      }
      if (!item) item = bundledTitle(id);
      if (needsJacketCrop(item || { id })) {
        const jacket = await jacketPosterResponse(id, item, fanart);
        if (jacket) return jacket;
      }
      // HK Cat III: real sehuatang image, then the looked-up cover, then
      // the generated file. A QR card or a dead host is not a poster.
      if (item && item.category === "hongkong") {
        if (/^https:\/\//i.test(String(item.forumPoster || ""))) {
          const forum = await cachedForumPoster(item.forumPoster);
          if (forum) return imageResponse(forum, sniffImageType(forum) || "image/jpeg");
        }
        if (item.poster && !/javstremio\.ddns\.net/i.test(item.poster)) {
          try {
            const remote = await fetchRemotePoster(item.poster);
            const buf = Buffer.from(await remote.arrayBuffer());
            const kind = sniffImageType(buf);
            const dim = kind === "image/jpeg" ? jpegSize(buf) : null;
            if (kind && buf.length > 4000 && !(dim && isForumPlaceholder(buf.length, dim.width, dim.height))) {
              return imageResponse(buf, kind);
            }
          } catch {
            /* generated file below */
          }
        }
        const own = await readOwnPoster(item.poster);
        if (own) return imageResponse(own, sniffImageType(own) || "image/jpeg");
        try {
          const file = await readFile(path.join(process.cwd(), "public", "posters", "hkcat3", `${id}.jpg`));
          return imageResponse(file, sniffImageType(file) || "image/jpeg");
        } catch {
          /* svg below */
        }
      }
      // JAV rows whose stored forum poster / DMM URL is dead or missing fall
      // back to the fourhoi cover, fetched server-side with no Referer
      // (fourhoi.com 403s on any Referer; no Referer → 200).
      const isJav = /^jav:/.test(id);
      const fourhoiUrl = isJav ? fourhoiPoster(id) : "";
      let src = (item && (item.forumPoster || item.poster)) || "";
      if (!src && !fourhoiUrl) {
        try {
          const metaBody = normalizeMetaBody(await fetchAddon(`meta/${type}/${id}`));
          src = (metaBody && metaBody.meta && (metaBody.meta.forumPoster || metaBody.meta.poster)) || "";
        } catch (e) {
          if (e.status === 404) return NextResponse.json({ error: "Not found" }, { status: 404 });
          throw e;
        }
      }
      if (!src && !fourhoiUrl) return NextResponse.json({ error: "Not found" }, { status: 404 });
      // hk: rows carry forum preview images on random CDN hosts; allow any
      // https host because the proxy fetches them server-side.
      let img;
      if (src) {
        try {
          img = await fetchRemotePoster(src, /^hk:/.test(id));
        } catch {
          // Dead forum-preview hosts (404/403): try the fourhoi cover for
          // JAV titles before giving up.
        }
      }
      if (!img && fourhoiUrl) {
        img = await fetchRemotePoster(fourhoiUrl);
      }
      if (!img) {
        // Dead forum-preview hosts (uploadhouse.com / pic2607v.cc 404):
        // serve the generated SVG title card instead of 502.
        if (/^hk:/.test(id) && item && item.name) {
          return new NextResponse(svgTitleCard(String(item.name)), {
            headers: {
              "Content-Type": "image/svg+xml",
              "Cache-Control": "public, max-age=86400, s-maxage=86400",
              "Access-Control-Allow-Origin": "*",
              "Cross-Origin-Resource-Policy": "cross-origin",
            },
          });
        }
        return NextResponse.json({ error: "Not found" }, { status: 404 });
      }
      const buf = await img.arrayBuffer();
      return new NextResponse(buf, {
        headers: {
          "Content-Type": img.headers.get("content-type") || "image/jpeg",
          "Cache-Control": "public, max-age=86400, s-maxage=86400",
          "Access-Control-Allow-Origin": "*",
          "Cross-Origin-Resource-Policy": "cross-origin",
        },
      });
    }
    if (seg0 === "play") {
      // Stremio's player GETs this URL. Resolve a fresh CDN link and 302 onto it.
      if (!token) {
        return NextResponse.json({ error: "Missing debrid token" }, { status: 400 });
      }
      const played = await resolvePlay(service, token, {
        torrentId: url.searchParams.get("torrent_id"),
        fileId: url.searchParams.get("file_id"),
        infoHash: url.searchParams.get("hash"),
        titleCode: url.searchParams.get("code"),
        clientIp: clientIp(req),
        cachedOnly: url.searchParams.get("cached") === "1",
      });
      if (played && played.success && played.url) {
        return new NextResponse(null, {
          status: 302,
          headers: {
            Location: played.url,
            "Cache-Control": "no-store",
            "Access-Control-Allow-Origin": "*",
          },
        });
      }
      if (rdShouldServeCachingClip(played)) {
        return new NextResponse(null, {
          status: 302,
          headers: {
            Location: `${publicOrigin(req, url)}/caching.mp4?v=2`,
            "Cache-Control": "no-store",
            "Access-Control-Allow-Origin": "*",
          },
        });
      }
      return NextResponse.json(
        { error: (played && played.error) || "Debrid is still adding the torrent" },
        { status: 502, headers: { "Cache-Control": "no-store" } },
      );
    }
    if (seg0 === "stream") {
      const rel = p.join("/");
      const titleCode = String(p[2] || "").replace(/\.json$/i, "");
      // hnt episode refs (hnt:<series_key>:<n>) are live SQLite rows, not static
      // addon files — skip the static-file fetch (which 404s and early-returns
      // empty streams) and build the stream list from the DB below.
      const isHntEpRef = /^hnt:[^:]+:\d+$/.test(titleCode);
      // HK Cat III rows live only in the live SQLite (no static stream files).
      const isHkRef = /^hk:/.test(titleCode);
      let streamJson = bundledStream(p[2] || "");
      if (!streamJson) {
        if (isHntEpRef) {
          streamJson = { streams: [] };
        } else if (isHkRef) {
          const db = getRouteDb();
          const hkRow = db ? queryTitle(db, titleCode) : null;
          streamJson = {
            streams: hkRow && hkRow.infoHash
              ? [{ infoHash: hkRow.infoHash, name: "Debrid", description: hkRow.name || "" }]
              : [],
          };
        } else {
          try {
            streamJson = await fetchAddon(rel);
          } catch (e) {
            if (e.status === 404) return NextResponse.json({ streams: [] });
            throw e;
          }
        }
      }
      const streams = (streamJson && streamJson.streams) || [];
      const first = streams[0] || {};
      const infoHash = first.infoHash || "";
      const name = first.name || "";
      const title = first.description || "";
      // Direct online playback (e.g. Hentaistream mp4 links) needs no debrid key.
      const directStream = streams.find((s) => s && s.url) || {};
      let directUrl = directStream.url || "";
      // An hnt episode ref (hnt:<series_key>:<n>) must be resolved to the
      // underlying row id (hnt:nyaa-<id>) so the lulu/debrid lookups find the row.
      let rowCode = titleCode;
      if (/^hnt:[^:]+:\d+$/.test(titleCode)) {
        const db = getRouteDb();
        const row = db ? queryEpisodeByRef(db, titleCode) : null;
        if (row) rowCode = row.id;
      }
      // Fallback for hnt rows that lack a Hentaistream mp4: play via the
      // Lulustream m3u8 proxied through this app (session-bound, resolved live).
      if (!directUrl) {
        const item = bundledTitle(rowCode);
        if (item && item.luluToken) {
          directUrl = `${publicOrigin(req, url)}/lulu/${item.luluToken}/master.m3u8`;
        }
      }
      // Hnt episodes: play the episode's own mp4 (stream_url). r2.1hanime.com
      // files sit behind Cloudflare bot management ("Attention Required"), so
      // route those through the /hntmp4 proxy (server-side fetch, optional
      // HNT_CF_COOKIE); the other mp4 hosts (streamhentai.org, hentaiera.com,
      // ...) are served directly.
      if (!directUrl && /^hnt:[^:]+:\d+$/.test(titleCode)) {
        const db = getRouteDb();
        const hntRow = db ? queryEpisodeByRef(db, titleCode) : null;
        const su = (hntRow && hntRow.stream_url) || "";
        if (su) {
          directUrl = su.includes("r2.1hanime.com")
            ? `${publicOrigin(req, url)}/hntmp4/${encodeURIComponent(titleCode)}`
            : su;
        }
      }
      // All debrid services work on every tier; a debrid row still only
      // shows for Torbox/Real-Debrid when the torrent is not cached.
      const debridOk = true;
      let cached = false;
      let torrentId = "";
      let fileId = "";
      let resolvedUrl = "";
      if (service && token && infoHash) {
        const look = await withTimeout(
          lookupDebridCache(service, token, infoHash, { titleCode: rowCode }),
          5000,
          { cached: false },
        );
        cached = Boolean(look && look.cached);
        torrentId = (look && look.torrentId) || "";
        fileId = (look && look.fileId) || "";
        const svc = String(service || "").toLowerCase().replace(/[\s-]+/g, "");
        if (svc.includes("realdebrid") && look && look.links && look.links.length) {
          resolvedUrl = await withTimeout(unlockRdLinks(token, look.links), 4500, "");
        }
      }
      return NextResponse.json(
        {
          ...STREAM_CACHE_HINTS,
          streams: buildAddonStreams({
            origin: publicOrigin(req, url),
            service,
            token,
            infoHash,
            torrentId,
            fileId,
            titleCode: rowCode,
            sourceName: name,
            sourceDescription: title,
            cached,
            resolvedUrl,
            includeDebrid: Boolean(debridOk && service && token && infoHash && includeDebridRow(service, cached)),
            directUrl,
            // hk:cat3 rows are only ever built live (no static stream files):
            // drop notWebReady so the P2P row shows in Stremio Web (matching
            // JAV's static 有码 rows, which carry no behavior hints). hnt keeps
            // the hint — Web plays those via their Online/directUrl rows.
            notWebReady: !isHkRef,
          }),
        },
        { headers: NO_STORE },
      );
    }
    if (seg0 === "lulu") {
      const luluToken = p[1] || "";
      const sub = p[2] || "";
      if (sub === "master.m3u8") {
        try {
          const body = await proxyM3u8(luluToken);
          return new NextResponse(body, {
            status: 200,
            headers: {
              "Content-Type": "application/vnd.apple.mpegurl",
              "Cache-Control": "no-store",
              "Access-Control-Allow-Origin": "*",
            },
          });
        } catch (e) {
          return NextResponse.json({ error: String(e.message || e) }, { status: 502 });
        }
      }
      if (sub === "seg" && p[3]) {
        const segPath = p.slice(3).join("/");
        try {
          const { isPlaylist, res } = await proxySegment(luluToken, segPath);
          const baseHeaders = { "Cache-Control": "no-store", "Access-Control-Allow-Origin": "*" };
          if (isPlaylist) {
            const body = rewritePlaylist(await res.text(), "/lulu/" + luluToken + "/seg");
            return new NextResponse(body, {
              status: 200,
              headers: { ...baseHeaders, "Content-Type": "application/vnd.apple.mpegurl" },
            });
          }
          const buf = Buffer.from(await res.arrayBuffer());
          return new NextResponse(buf, {
            status: 200,
            headers: {
              ...baseHeaders,
              "Content-Type": res.headers.get("content-type") || "video/mp4",
              "Accept-Ranges": "bytes",
            },
          });
        } catch (e) {
          return NextResponse.json({ error: String(e.message || e) }, { status: 502 });
        }
      }
    }
    if (seg0 === "hntmp4") {
      // Proxy for hnt episode mp4s on r2.1hanime.com (Cloudflare bot
      // management: "Attention Required"). The server fetches with
      // browser-like headers; if CF still blocks, set the HNT_CF_COOKIE env
      // (a cf_clearance cookie from a real 1hanime.com session) on the
      // server. Streams the body directly (no full-file buffering) and
      // passes the client's Range request through so the player can seek.
      const ref = decodeURIComponent(p[1] || "");
      const db = getRouteDb();
      const row = db && /^hnt:[^:]+:\d+$/.test(ref) ? queryEpisodeByRef(db, ref) : null;
      const su = (row && row.stream_url) || "";
      if (!su || !/^https?:/.test(su)) {
        return NextResponse.json({ error: "No stream_url for this episode" }, { status: 404 });
      }
      const headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Referer": "https://1hanime.com/",
        "Accept": "video/mp4, video/*, */*",
      };
      const cfCookie = process.env.HNT_CF_COOKIE || "";
      if (cfCookie) headers["Cookie"] = cfCookie;
      const range = req.headers.get("range");
      if (range) headers["Range"] = range;
      let up;
      try {
        up = await fetch(su, { headers, redirect: "follow" });
      } catch (e) {
        return NextResponse.json({ error: String(e.message || e) }, { status: 502 });
      }
      const baseHeaders = {
        "Content-Type": up.headers.get("content-type") || "video/mp4",
        "Cache-Control": "no-store",
        "Access-Control-Allow-Origin": "*",
        "Accept-Ranges": "bytes",
      };
      if (up.status === 206) baseHeaders["Content-Range"] = up.headers.get("content-range") || "";
      if (up.status === 403 && !cfCookie) {
        return NextResponse.json(
          { error: "Upstream Cloudflare 403 — set HNT_CF_COOKIE (cf_clearance) on the server", cfBlocked: true },
          { status: 502, headers: { "Cache-Control": "no-store", "Access-Control-Allow-Origin": "*" } },
        );
      }
      if (!up.ok && up.status !== 206) {
        return NextResponse.json(
          { error: `Upstream ${up.status}`, upstreamStatus: up.status },
          { status: 502, headers: { "Cache-Control": "no-store" } },
        );
      }
      return new NextResponse(up.body, {
        status: up.status === 206 ? 206 : 200,
        headers: baseHeaders,
      });
    }

    return NextResponse.json({ error: "Not found" }, { status: 404 });
  } catch (e) {
    return NextResponse.json({ error: String(e.message || e) }, { status: 502 });
  }
}

export async function HEAD(req, { params }) {
  const p = params.p || [];
  // Return 200 so Stremio still GET-plays. Also add/stop on HEAD — some
  // clients never GET if HEAD looks like an empty mp4. hntmp4 keeps HEAD
  // cheap (no upstream fetch): a 200 video/mp4 is enough for the player.
  if (p[0] === "play" || p[0] === "hntmp4") {
    const url = new URL(req.url);
    const token = url.searchParams.get("token") || "";
    const service = url.searchParams.get("service") || "";
    const hash = url.searchParams.get("hash") || "";
    // HEAD must stay 200 video/mp4 and must not add magnets. Real-Debrid
    // created a new list row on every HEAD+GET pair and then played the
    // unfinished copy (Caching clip) even when a downloaded torrent existed.
    const svc = String(service || "").toLowerCase().replace(/[\s-]+/g, "");
    if (token && hash && !svc.includes("realdebrid")) {
      try {
        await resolveDebrid(service, token, hash, "", {
          cachedOnly: url.searchParams.get("cached") === "1",
        });
      } catch {
        // HEAD must still be 200 video/mp4.
      }
    }
    return new NextResponse(null, {
      status: 200,
      headers: {
        "Content-Type": "video/mp4",
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
        "Access-Control-Allow-Origin": "*",
      },
    });
  }
  return GET(req, { params });
}

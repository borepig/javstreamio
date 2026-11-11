import { NextResponse } from "next/server";
import {
  fetchAddon,
  fetchRemotePoster,
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
      const catalogId = resolveCatalogId(p[2] || "jav-censored");
      const extra = String(p[3] || "").replace(/\.json$/, "");
      const { skip: pathSkip, search: pathSearch } = parseCatalogExtra(extra);
      // Stremio sends skip/search as URL query params (standard protocol);
      // also honor the legacy path-based extra.
      const qSkip = Number(url.searchParams.get("skip") || 0);
      const qSearch = url.searchParams.get("search") || "";
      const skip = Math.max(pathSkip, qSkip);
      const search = qSearch || pathSearch;
      // Free tier: newest FREE_CATALOG_SIZE per catalog. Paid tier: full catalog.
      // Paid-only categories (HK AV, hentai, Korean, Chinese) return an empty
      // catalog for free users.
      if (!paid && PAID_ONLY_CATEGORIES.includes(catalogId)) {
        return NextResponse.json({ metas: [] }, { headers: NO_STORE });
      }
      const body = withPublicPosters(
        bundledCatalog(catalogId, skip, paid ? undefined : FREE_CATALOG_SIZE, search),
        "catalog",
        publicOrigin(req, url),
      );
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
      }
      if (!item) item = bundledTitle(id);
      let src = (item && (item.forumPoster || item.poster)) || "";
      if (!src) {
        try {
          const metaBody = normalizeMetaBody(await fetchAddon(`meta/${type}/${id}`));
          src = (metaBody && metaBody.meta && (metaBody.meta.forumPoster || metaBody.meta.poster)) || "";
        } catch (e) {
          if (e.status === 404) return NextResponse.json({ error: "Not found" }, { status: 404 });
          throw e;
        }
      }
      if (!src) return NextResponse.json({ error: "Not found" }, { status: 404 });
      const img = await fetchRemotePoster(src);
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
      let streamJson = bundledStream(p[2] || "");
      if (!streamJson) {
        if (isHntEpRef) {
          streamJson = { streams: [] };
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
      let resolvedUrl = "";
      if (service && token && infoHash) {
        const look = await withTimeout(
          lookupDebridCache(service, token, infoHash, { titleCode: rowCode }),
          5000,
          { cached: false },
        );
        cached = Boolean(look && look.cached);
        torrentId = (look && look.torrentId) || "";
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
            titleCode: rowCode,
            sourceName: name,
            sourceDescription: title,
            cached,
            resolvedUrl,
            includeDebrid: Boolean(debridOk && service && token && infoHash && includeDebridRow(service, cached)),
            directUrl,
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

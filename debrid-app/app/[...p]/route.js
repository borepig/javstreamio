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
import { ADDON_ID, ADDON_NAME, ADDON_VERSION } from "../../lib/version.js";

export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";
export const revalidate = 0;
export const maxDuration = 10;

// Addon files change when the JAVSTREAMIO repo is updated (new metas, version bumps),
// so tell the Vercel edge (and any intermediary) not to cache the proxied responses.
// "no-cache, no-store, must-revalidate" defeats both the Vercel edge cache and any
// intermediary HTTP cache, so a manifest version bump propagates immediately.
const NO_STORE = { "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0, s-maxage=0" };

function clientIp(req) {
  const forwarded = req.headers.get("x-forwarded-for") || "";
  return forwarded.split(",")[0].trim() || req.headers.get("x-real-ip") || "";
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
      const { skip, search } = parseCatalogExtra(extra);
      const body = withPublicPosters(bundledCatalog(catalogId, skip, undefined, search), "catalog", url.origin);
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
      body = withPublicPosters(normalizeMetaBody(body), "meta", url.origin);
      return NextResponse.json(body, { headers: NO_STORE });
    }
    if (seg0 === "poster") {
      const type = p[1] || "movie";
      const id = decodeURIComponent((p[2] || "").replace(/\.(jpe?g|png|webp)$/i, ""));
      if (!id) return NextResponse.json({ error: "Not found" }, { status: 404 });
      const item = bundledTitle(id);
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
            Location: `${url.origin}/caching.mp4?v=2`,
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
      let streamJson = bundledStream(p[2] || "");
      if (!streamJson) {
        try {
          streamJson = await fetchAddon(rel);
        } catch (e) {
          if (e.status === 404) return NextResponse.json({ streams: [] });
          throw e;
        }
      }
      const streams = (streamJson && streamJson.streams) || [];
      const first = streams[0] || {};
      const infoHash = first.infoHash || "";
      const name = first.name || "";
      const title = first.description || "";
      const titleCode = String(p[2] || "").replace(/\.json$/i, "");
      let cached = false;
      let torrentId = "";
      let resolvedUrl = "";
      if (service && token && infoHash) {
        const look = await withTimeout(
          lookupDebridCache(service, token, infoHash, { titleCode }),
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
            origin: url.origin,
            service,
            token,
            infoHash,
            torrentId,
            titleCode,
            sourceName: name,
            sourceDescription: title,
            cached,
            resolvedUrl,
            includeDebrid: Boolean(service && token && infoHash && includeDebridRow(service, cached)),
          }),
        },
        { headers: NO_STORE },
      );
    }

    return NextResponse.json({ error: "Not found" }, { status: 404 });
  } catch (e) {
    return NextResponse.json({ error: String(e.message || e) }, { status: 502 });
  }
}

export async function HEAD(req, { params }) {
  const p = params.p || [];
  // Return 200 so Stremio still GET-plays. Also add/stop on HEAD — some
  // clients never GET if HEAD looks like an empty mp4.
  if (p[0] === "play") {
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

import { NextResponse } from "next/server";
import { fetchAddon } from "../../lib/addon-data.js";
import { resolveDebrid } from "../../lib/debrids.js";

export const dynamic = "force-dynamic";

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
      const m = await fetchAddon("manifest.json");
      return NextResponse.json(m);
    }
    if (seg0 === "catalog" || seg0 === "meta") {
      const rel = p.join("/");
      let body;
      try {
        body = await fetchAddon(rel);
      } catch (e) {
        if (e.status === 404) {
          // Stremio's default catalog request is /catalog/movie (no file); serve the
          // only catalog we have. Other missing catalog paths get an empty catalog.
          if (seg0 === "catalog" && rel === "catalog/movie") {
            body = await fetchAddon("catalog/movie/jav103.json");
          } else if (seg0 === "catalog") {
            return NextResponse.json({ metas: [] });
          } else {
            return NextResponse.json({ error: "Not found" }, { status: 404 });
          }
        } else {
          throw e;
        }
      }
      return NextResponse.json(body);
    }
    if (seg0 === "stream") {
      const rel = p.join("/");
      let streamJson;
      try {
        streamJson = await fetchAddon(rel);
      } catch (e) {
        if (e.status === 404) return NextResponse.json({ streams: [] });
        throw e;
      }
      const streams = (streamJson && streamJson.streams) || [];
      const first = streams[0] || {};
      const infoHash = first.infoHash || "";
      const magnet = first.magnet || "";
      const name = first.name || "";

      // If the client passes ?service=&token=, resolve the infoHash through that debrid.
      // wait:false keeps the request fast (Vercel serverless caps at 10s) — if the torrent
      // isn't cached, the magnet fallback below is returned instead of polling 30s.
      if (infoHash && service && token) {
        const res = await resolveDebrid(service, token, infoHash, magnet, { wait: false });
        const out = [];
        if (res && res.success && res.url) {
          out.push({ name, url: res.url, protocol: "http", source: service });
        } else if (magnet) {
          out.push({ name, url: magnet, protocol: "magnet" });
        } else if (infoHash) {
          out.push({ name, infoHash });
        }
        return NextResponse.json({ streams: out });
      }

      // No service/token → return the static stream (infoHash or magnet) so Stremio's
      // built-in BitTorrent engine can play it.
      const out = [];
      if (infoHash) out.push({ name, infoHash });
      if (magnet) out.push({ name: (name || "magnet"), url: magnet, protocol: "magnet" });
      return NextResponse.json({ streams: out });
    }

    return NextResponse.json({ error: "Not found" }, { status: 404 });
  } catch (e) {
    return NextResponse.json({ error: String(e.message || e) }, { status: 502 });
  }
}

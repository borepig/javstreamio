import { NextResponse } from "next/server";
import { resolveDebrid } from "../../../lib/debrids.js";
import { fetchAddon } from "../../../lib/addon-data.js";

const HASH_RE = /urn:btih:([A-Za-z0-9]+)/i;

function extractHash(input) {
  const s = String(input || "").trim();
  const m = HASH_RE.exec(s);
  if (m) return m[1].toUpperCase();
  if (/^[A-Za-z0-9]{32,40}$/.test(s)) return s.toUpperCase();
  return "";
}

// POST { service, key, metaId?|hash } -> { success, url } | { success:false, error }
// With metaId, the magnet is looked up from the scraped JSONL index (no manual
// hash entry needed); the bare hash field remains for ad-hoc magnets.
export async function POST(req) {
  const body = await req.json().catch(() => ({}));
  const service = body.service || "";
  const key = body.key || "";
  const input = String(body.hash || "").trim();
  const metaId = String(body.metaId || "").trim();

  if (!service || !key) {
    return NextResponse.json({ success: false, error: "service and key are required" }, { status: 400 });
  }

  let infoHash = extractHash(input);
  let magnet = input.toLowerCase().startsWith("magnet:") ? input : "";

  if (metaId) {
    // Read this title's own stream file (now carries its magnet) — no whole-catalog load.
    const streamJson = await fetchAddon(`stream/movie/${metaId}.json`).catch(() => null);
    const sStreams = (streamJson && streamJson.streams) || [];
    const first = sStreams[0] || {};
    if (first.magnet) {
      magnet = first.magnet;
      infoHash = first.infoHash || extractHash(first.magnet) || infoHash;
    }
  }

  if (!infoHash) {
    return NextResponse.json(
      {
        success: false,
        error: "Enter a hash or magnet link above, or use the Stremio addon link (shown on the page) — titles resolve automatically in Stremio.",
      },
      { status: 400 }
    );
  }

  const result = await resolveDebrid(service, key, infoHash, magnet);
  if (result.success && result.url) {
    return NextResponse.json({ success: true, url: result.url, service });
  }
  return NextResponse.json({ success: false, error: result.error || "Resolution failed" });
}

export async function OPTIONS() {
  return NextResponse.json({ ok: true });
}

import { NextResponse } from "next/server";
import { resolveDebrid } from "../../../lib/debrids.js";

const HASH_RE = /urn:btih:([A-Za-z0-9]+)/i;

function extractHash(input) {
  const s = String(input || "").trim();
  const m = HASH_RE.exec(s);
  if (m) return m[1].toUpperCase();
  if (/^[A-Za-z0-9]{32,40}$/.test(s)) return s.toUpperCase();
  return "";
}

// POST { service, key, hash } -> { success, url } | { success:false, error }
export async function POST(req) {
  const body = await req.json().catch(() => ({}));
  const service = body.service || "";
  const key = body.key || "";
  const input = String(body.hash || "").trim();

  if (!service || !key) {
    return NextResponse.json({ success: false, error: "service and key are required" }, { status: 400 });
  }
  const infoHash = extractHash(input);
  if (!infoHash) {
    return NextResponse.json({ success: false, error: "Could not parse a torrent hash from the input" }, { status: 400 });
  }
  const isMagnet = input.toLowerCase().startsWith("magnet:");
  const magnet = isMagnet ? input : "";

  const result = await resolveDebrid(service, key, infoHash, magnet);
  if (result.success && result.url) {
    return NextResponse.json({ success: true, url: result.url, service });
  }
  return NextResponse.json({ success: false, error: result.error || "Resolution failed" });
}

export async function OPTIONS() {
  return NextResponse.json({ ok: true });
}

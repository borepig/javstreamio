// Local smoke test: drive the real catch-all route handler with fake
// requests and check the hnt series/episode endpoints end-to-end.
import { GET } from "./app/[...p]/route.js";

const KEY = "javpro-2d9e5748";

function makeReq(url) {
  return { url, headers: new Headers() };
}

async function get(path, key = "") {
  const qs = key ? `?key=${key}` : "";
  const url = `http://localhost:3000${path}${qs}`;
  const res = await GET(makeReq(url), { params: { p: path.split("/").filter(Boolean) } });
  const ct = String(res.headers.get("content-type") || "");
  if (ct.includes("application/json")) {
    return { status: res.status, body: await res.json() };
  }
  const buf = await res.arrayBuffer();
  return { status: res.status, body: `<${ct || "binary"} ${buf.byteLength}b>`, location: res.headers.get("location") || "" };
}

const cases = [
  ["GET /manifest.json (free)", "/manifest.json", ""],
  ["GET /catalog/series/hentai-censored.json (paid)", "/catalog/series/hentai-censored.json", KEY],
  ["GET /catalog/series/hentai-uncensored.json (paid)", "/catalog/series/hentai-uncensored.json", KEY],
  ["GET /meta/series/hnt:fc2.json (paid)", "/meta/series/hnt:fc2.json", KEY],
  ["GET /meta/episode/hnt:fc2:1.json (paid)", "/meta/episode/hnt:fc2:1.json", KEY],
  ["GET /stream/episode/hnt:fc2:1.json (paid)", "/stream/episode/hnt:fc2:1.json", KEY],
  ["GET /catalog/series/hentai-censored.json (free)", "/catalog/series/hentai-censored.json", ""],
];

for (const [label, path, key] of cases) {
  const { status, body, location } = await get(path, key);
  let summary;
  if (typeof body === "string") {
    summary = body + (location ? ` -> ${location}` : "");
  } else if (body && body.meta) {
    const m = body.meta;
    summary = `meta: ${m.id} type=${m.type} name=${(m.name || "").slice(0, 40)} episodes=${m.episodes?.length ?? 0}`;
  } else if (body && body.metas) {
    const m0 = body.metas[0] || {};
    summary = `${body.metas.length} metas; first: ${m0.id} type=${m0.type}`;
  } else if (body && body.version) {
    summary = `version=${body.version} catalogs=${(body.catalogs || []).length}`;
  } else if (body && body.streams) {
    const s0 = body.streams[0] || {};
    summary = `${body.streams.length} streams; first: ${s0.name || s0.infoHash || JSON.stringify(s0).slice(0, 60)}`;
  } else {
    summary = JSON.stringify(body).slice(0, 120);
  }
  console.log(`[${status}] ${label} -> ${summary}`);
}
console.log("smoke done");

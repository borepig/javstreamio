// Serves the static Stremio addon files (manifest, catalog, meta, stream) by proxying them
// from the GitHub raw URL of the javstreamio repo. Each title's own stream file carries its
// magnet, so the app never loads the whole catalog at once.

import bundledTitlesJson from "../data/titles.json" with { type: "json" };
import { ADDON_ID, ADDON_NAME, ADDON_VERSION } from "./version.js";
import { openTitlesDb, queryCatalog, queryTitle, resolveSqlitePath, searchText, searchTokens } from "./titles-db.js";

const RAW = "https://raw.githubusercontent.com/borepig/javstreamio/main/addon";
const cache = new Map(); // url -> { body, ts }
const TTL = 60_000; // 60s: bounds the in-memory cache so new addon versions propagate

export function rawUrl(relPath) {
  // Append a per-request cache-buster so the GitHub raw (CloudFront) CDN can't serve a
  // stale cached copy — forces a fresh origin fetch of the latest addon files.
  return `${RAW}/${relPath}?nc=${Date.now()}`;
}

// Stremio's ResourceResponse.Meta is `{ meta: <object> }`. The static GitHub files were
// historically written as a bare Meta Object (or `{ meta: [object] }`); either shape
// makes stremio-core drop the response and the client shows "No metadata was found!".
function rfc3339Released(value) {
  if (!value || typeof value !== "string") return null;
  const text = value.trim();
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(text)) {
    return /Z|[+-]\d{2}:\d{2}$/.test(text) ? text : `${text}Z`;
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return `${text}T00:00:00.000Z`;
  return null;
}

export function normalizeMetaBody(body) {
  if (!body || typeof body !== "object" || Array.isArray(body)) return body;
  const inner = body.meta;
  let meta;
  if (inner && typeof inner === "object" && !Array.isArray(inner)) {
    meta = inner;
  } else if (Array.isArray(inner) && inner[0] && typeof inner[0] === "object") {
    meta = inner[0];
  } else if (body.id && body.type) {
    meta = body;
  } else {
    return body;
  }
  const released = rfc3339Released(meta.released);
  if (released) meta.released = released;
  else delete meta.released;
  if (meta.id) {
    const hints = { ...(meta.behaviorHints || {}) };
    if (!hints.defaultVideoId) hints.defaultVideoId = meta.id;
    if (hints.hasScheduledVideos == null) hints.hasScheduledVideos = false;
    meta.behaviorHints = hints;
  }
  return { meta: withStremioCredits(meta) };
}

const POSTER_HOSTS = ["tu.djhdhs.us", "jdbstatic.com"];
const POSTER_REFERER = "https://sehuatang.org/";
const POSTER_UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36";

export function isAllowedPosterUrl(src) {
  try {
    const parsed = new URL(src);
    if (parsed.protocol !== "https:") return false;
    const host = parsed.hostname.toLowerCase();
    return POSTER_HOSTS.some((allowed) => host === allowed || host.endsWith("." + allowed));
  } catch {
    return false;
  }
}

export function posterPathFor(type, id) {
  return `/poster/${type}/${encodeURIComponent(id)}`;
}

export function dmmPoster(idOrTitle, size = "pl") {
  const raw = String(idOrTitle || "").replace(/^jav:/i, "").trim();
  if (/^thread/i.test(raw) || /^fc2/i.test(raw)) return "";
  const match = raw.match(/^([A-Za-z]{2,})[-_]?0*(\d+)$/);
  if (!match) return "";
  const code = match[1].toLowerCase() + match[2].padStart(5, "0");
  const suffix = size === "ps" ? "ps" : "pl";
  return `https://pics.dmm.co.jp/digital/video/${code}/${code}${suffix}.jpg`;
}

const MONO1_PREFIX = /^(akdld|dldss|fns|mist|nhdtc|stars|start|sw)$/i;

export function dmmMonoPoster(idOrTitle, size = "ps") {
  const raw = String(idOrTitle || "").replace(/^jav:/i, "").trim();
  const match = raw.match(/^([A-Za-z]{2,})[-_](\d+)$/);
  if (!match) return "";
  const prefix = match[1].toLowerCase();
  if (!MONO1_PREFIX.test(prefix)) return "";
  const cid = `1${prefix}${String(Number(match[2]))}`;
  const suffix = size === "pl" ? "pl" : "ps";
  return `https://pics.dmm.co.jp/mono/movie/adult/${cid}/${cid}${suffix}.jpg`;
}

export function fourhoiPoster(idOrTitle) {
  const raw = String(idOrTitle || "").replace(/^jav:/i, "").trim();
  if (/^thread/i.test(raw) || /^fc2/i.test(raw)) return "";
  const match = raw.match(/^([A-Za-z]{2,})[-_](\d+)$/);
  if (!match) return "";
  return `https://fourhoi.com/${match[1].toLowerCase()}-${match[2]}/cover-n.jpg`;
}

const PRESTIGE_PREFIX = /^(abf|abp|abs|aka|blo|chn|dic|dom|edd|mega|ppt|sga|tre|wpc|yrh)$/i;

export function mgstagePoster(idOrTitle) {
  const raw = String(idOrTitle || "").replace(/^jav:/i, "").trim();
  const match = raw.match(/^([A-Za-z]{2,})[-_](\d+)$/);
  if (!match) return "";
  const prefix = match[1].toLowerCase();
  if (!PRESTIGE_PREFIX.test(prefix)) return "";
  const num = String(Number(match[2]));
  return `https://image.mgstage.com/images/prestige/${prefix}/${num}/pb_e_${prefix}-${match[2]}.jpg`;
}

export function isPublicArtUrl(src) {
  try {
    const host = new URL(src).hostname.toLowerCase();
    if (host.includes("vercel.app") || host.includes("djhdhs.us") || host.includes("w4kmzkm.com")) {
      return false;
    }
    return (
      host === "pics.dmm.co.jp" ||
      host === "fourhoi.com" ||
      host.endsWith(".fourhoi.com") ||
      host === "image.mgstage.com" ||
      host === "jdbstatic.com" ||
      host.endsWith(".jdbstatic.com")
    );
  } catch {
    return false;
  }
}

function forumPosterUrl(item, origin) {
  if (!origin || !item || !item.id) return "";
  if (!item.forumPoster && !String(item.poster || "").includes("djhdhs.us")) return "";
  return `${origin}${posterPathFor(item.type || "movie", item.id)}`;
}

function applyPublicArt(item, kind, origin) {
  if (!item) return item;
  const next = { ...item };
  const skipDmm = Boolean(item.dmmMissing);
  const ps = skipDmm ? "" : dmmPoster(item.id, "ps");
  const pl = skipDmm ? "" : dmmPoster(item.id, "pl");
  const fourhoi = item.fourhoiMissing ? "" : fourhoiPoster(item.id);
  const mgstage = mgstagePoster(item.id);
  const monoPs = dmmMonoPoster(item.id, "ps");
  const monoPl = dmmMonoPoster(item.id, "pl");
  const proxied = forumPosterUrl(item, origin);
  if (kind === "catalog") {
    if (ps) next.poster = ps;
    else if (mgstage) next.poster = mgstage;
    else if (monoPs) next.poster = monoPs;
    else if (fourhoi) next.poster = fourhoi;
    else if (proxied) next.poster = proxied;
    else if (!isPublicArtUrl(next.poster)) delete next.poster;
    delete next.background;
    delete next.forumPoster;
    delete next.dmmMissing;
    delete next.fourhoiMissing;
    return next;
  }
  if (ps) next.poster = ps;
  else if (mgstage) next.poster = mgstage;
  else if (monoPs) next.poster = monoPs;
  else if (fourhoi) next.poster = fourhoi;
  else if (proxied) next.poster = proxied;
  else if (!isPublicArtUrl(next.poster)) delete next.poster;
  if (pl) next.background = pl;
  else if (mgstage) next.background = mgstage;
  else if (monoPl) next.background = monoPl;
  else if (fourhoi) next.background = fourhoi;
  else if (proxied) next.background = proxied;
  else if (!isPublicArtUrl(next.background)) delete next.background;
  delete next.forumPoster;
  delete next.dmmMissing;
  delete next.fourhoiMissing;
  return next;
}

// JAV: DMM digital if real, MGStage, DMM mono, fourhoi, then sehuatang /poster.
// Western/Others use sehuatang through /poster.
export function withPublicPosters(body, kind, origin = "") {
  if (!body || typeof body !== "object") return body;
  if (kind === "catalog" && Array.isArray(body.metas)) {
    return { ...body, metas: body.metas.map((item) => applyPublicArt(item, "catalog", origin)) };
  }
  if (kind === "meta" && body.meta) {
    return { ...body, meta: applyPublicArt(body.meta, "meta", origin) };
  }
  return body;
}

const CATALOG_EXTRA = [
  { name: "search", isRequired: false },
  { name: "skip", isRequired: false },
];

export const BUNDLED_CATALOGS = [
  { type: "movie", id: "jav-censored", name: "JAV Censored", extra: CATALOG_EXTRA },
  { type: "movie", id: "jav-uncensored", name: "JAV Uncensored", extra: CATALOG_EXTRA },
  { type: "movie", id: "western", name: "Western", extra: CATALOG_EXTRA },
  { type: "movie", id: "others", name: "Others", extra: CATALOG_EXTRA },
];

const CATALOG_PAGE = 80;

export function parseCatalogExtra(extra) {
  const raw = String(extra || "").replace(/\.json$/i, "");
  const params = new URLSearchParams(raw);
  const skip = Number(params.get("skip") || 0);
  return {
    search: (params.get("search") || "").trim(),
    skip: Number.isFinite(skip) && skip > 0 ? skip : 0,
  };
}

let titlesDb;

function getTitlesDb() {
  if (titlesDb !== undefined) return titlesDb;
  try {
    const file = resolveSqlitePath();
    titlesDb = file ? openTitlesDb(file) : null;
  } catch {
    titlesDb = null;
  }
  return titlesDb;
}

function loadBundledTitles() {
  return bundledTitlesJson || {};
}

export function resolveCatalogId(catalogId) {
  const id = String(catalogId || "").replace(/\.json$/, "");
  if (id === "jav103" || !id) return "jav-censored";
  return id;
}

const FACT_LINE_RE = /^(出演|類型|類別|導演|監督|時長|發行商?|JavDB評分|片商|容量|有码\/无码)\s*[:：]/;
const SIZE_RE = /^\d+(?:\.\d+)?\s*(?:[KMGT]B|[KMGT]iB|[GT])$/i;

export function asStremioRuntime(value) {
  const text = String(value || "").trim();
  if (!text || SIZE_RE.test(text)) return "";
  const match = text.match(/^(\d+)\s*m(?:in(?:utes?)?)?$/i);
  return match ? `${match[1]} min` : text;
}

export function plotDescription(description) {
  const extras = [];
  const plot = [];
  for (const raw of String(description || "").split(/\n/)) {
    const line = raw.trim();
    if (!line) {
      if (plot.length && plot[plot.length - 1] !== "") plot.push("");
      continue;
    }
    const fact = line.match(FACT_LINE_RE);
    if (fact) {
      if (/^(片商|容量|有码\/无码)$/.test(fact[1])) extras.push(line);
      continue;
    }
    plot.push(line);
  }
  const text = plot.join("\n").replace(/\n{3,}/g, "\n\n").trim();
  return extras.length ? `${text}\n\n${extras.join("\n")}`.trim() : text;
}

function creditNames(value) {
  const list = Array.isArray(value) ? value : value ? [value] : [];
  const names = [];
  for (const item of list) {
    for (const part of String(item).split(/[/、,，]+/)) {
      const name = part.trim();
      if (name && name.toUpperCase() !== "N/A") names.push(name);
    }
  }
  return names;
}

export function metaLinks({ id, cast, genres, director, imdbRating }) {
  const links = [];
  if (imdbRating) {
    const code = String(id || "").replace(/^jav:/i, "");
    links.push({
      name: String(imdbRating),
      category: "imdb",
      url: `https://javdb.com/search?q=${encodeURIComponent(code)}&f=all`,
    });
  }
  for (const genre of genres || []) {
    if (!genre) continue;
    links.push({
      name: genre,
      category: "Genres",
      url: `stremio:///search?search=${encodeURIComponent(genre)}`,
    });
  }
  for (const name of creditNames(director)) {
    links.push({
      name,
      category: "Directors",
      url: `stremio:///search?search=${encodeURIComponent(name)}`,
    });
  }
  for (const name of creditNames(cast)) {
    links.push({
      name,
      category: "Cast",
      url: `stremio:///search?search=${encodeURIComponent(name)}`,
    });
  }
  return links;
}

export function withStremioCredits(meta) {
  if (!meta || typeof meta !== "object") return meta;
  const runtime = asStremioRuntime(meta.runtime);
  const cast = creditNames(meta.cast);
  const genres = Array.isArray(meta.genres) ? meta.genres.filter(Boolean) : [];
  const next = {
    ...meta,
    description: plotDescription(meta.description),
  };
  if (runtime) next.runtime = runtime;
  else delete next.runtime;
  if (cast.length) next.cast = cast;
  if (genres.length) {
    next.genres = genres;
    next.genre = genres;
  }
  const links = metaLinks({
    id: meta.id,
    cast,
    genres,
    director: meta.director,
    imdbRating: meta.imdbRating,
  });
  if (links.length) next.links = links;
  return next;
}

function titleMatchesSearch(item, search) {
  const tokens = searchTokens(search);
  if (!tokens.length) return true;
  const hay = searchText({
    id: item.id,
    name: item.name,
    cast: item.cast,
    genres: item.genres,
  });
  return tokens.every((token) => hay.includes(token));
}

export function bundledTitle(id) {
  const want = decodeURIComponent(String(id || "").replace(/\.json$/, ""));
  const db = getTitlesDb();
  if (db) {
    const hit = queryTitle(db, want);
    if (hit) return hit;
  }
  const data = loadBundledTitles();
  for (const list of Object.values(data)) {
    if (!Array.isArray(list)) continue;
    const hit = list.find((item) => item && item.id === want);
    if (hit) return hit;
  }
  return null;
}

export function bundledCatalog(catalogId, skip = 0, page = CATALOG_PAGE, search = "") {
  const key = resolveCatalogId(catalogId);
  const db = getTitlesDb();
  const list = db
    ? queryCatalog(db, { catalogId: key, skip, page, search })
    : (loadBundledTitles()[key] || []).filter((item) => titleMatchesSearch(item, search)).slice(skip, skip + page);
  return {
    metas: list.map((item) => {
      const preview = {
        id: item.id,
        type: "movie",
        name: item.name,
        releaseInfo: item.releaseInfo || "",
      };
      if (item.poster) preview.poster = item.poster;
      if (item.forumPoster) preview.forumPoster = item.forumPoster;
      if (item.dmmMissing) preview.dmmMissing = true;
      if (item.fourhoiMissing) preview.fourhoiMissing = true;
      return preview;
    }),
  };
}

export function bundledMeta(id) {
  const item = bundledTitle(id);
  if (!item) return null;
  const meta = {
    id: item.id,
    type: "movie",
    name: item.name,
    description: item.description || "",
    releaseInfo: item.releaseInfo || "",
    runtime: item.runtime || "",
  };
  if (item.poster) meta.poster = item.poster;
  if (item.forumPoster) meta.forumPoster = item.forumPoster;
  if (item.dmmMissing) meta.dmmMissing = true;
  if (item.fourhoiMissing) meta.fourhoiMissing = true;
  if (item.background) meta.background = item.background;
  if (item.released) meta.released = item.released;
  if (item.cast) meta.cast = item.cast;
  if (item.genres) meta.genres = item.genres;
  if (item.director) meta.director = item.director;
  if (item.imdbRating) meta.imdbRating = item.imdbRating;
  return { meta: withStremioCredits(meta) };
}

export function bundledStream(id) {
  const item = bundledTitle(id);
  if (!item || !item.infoHash) return null;
  return {
    streams: [
      {
        name: item.runtime || "",
        infoHash: item.infoHash,
        magnet: item.magnet || "",
        description: item.name || "",
      },
    ],
  };
}

export function withBundledCatalogs(manifest) {
  if (!manifest || typeof manifest !== "object") return manifest;
  return {
    ...manifest,
    id: ADDON_ID,
    name: ADDON_NAME,
    version: ADDON_VERSION,
    description: `${ADDON_NAME} ${ADDON_VERSION}. This is only a metadata scraper.`,
    catalogs: BUNDLED_CATALOGS,
  };
}

export function withProxiedPosters(body, origin, kind) {
  if (!body || typeof body !== "object") return body;
  if (kind === "catalog" && Array.isArray(body.metas)) {
    return {
      ...body,
      metas: body.metas.map((item) => {
        if (!item || !item.id || !item.poster) return item;
        return { ...item, poster: `${origin}${posterPathFor(item.type || "movie", item.id)}` };
      }),
    };
  }
  if (kind === "meta" && body.meta && body.meta.id && body.meta.poster) {
    const type = body.meta.type || "movie";
    return {
      ...body,
      meta: { ...body.meta, poster: `${origin}${posterPathFor(type, body.meta.id)}` },
    };
  }
  return body;
}

export async function fetchRemotePoster(src) {
  if (!isAllowedPosterUrl(src)) {
    const err = new Error("Poster host not allowed");
    err.status = 400;
    throw err;
  }
  const res = await fetch(src, {
    headers: {
      "User-Agent": POSTER_UA,
      Referer: POSTER_REFERER,
      Accept: "image/jpeg,image/png,image/*;q=0.5",
    },
    redirect: "follow",
  });
  if (!res.ok) {
    const err = new Error(`Poster fetch failed: HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return res;
}

export async function fetchAddon(relPath) {
  // Stremio requests file paths without an extension (e.g. "meta/movie/jav:WAAA-629"), but the
  // addon files on GitHub raw are stored with a ".json" extension. Append it when missing so the
  // proxied fetch hits the real file instead of 404.
  const fileRel = relPath.endsWith(".json") ? relPath : relPath + ".json";
  const url = rawUrl(fileRel);
  // The manifest is always fetched fresh so a version bump propagates immediately.
  const isManifest = relPath === "manifest.json";
  if (!isManifest) {
    const hit = cache.get(relPath);
    if (hit && Date.now() - hit.ts < TTL) return hit.body;
  }
  const res = await fetch(url, { cache: "no-store", next: { revalidate: 0 } });
  if (!res.ok) {
    const err = new Error(`Failed to fetch ${fileRel}: HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  const body = await res.json();
  cache.set(relPath, { body, ts: Date.now() });
  return body;
}

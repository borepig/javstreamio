// Serves the static Stremio addon files (manifest, catalog, meta, stream) by proxying them
// from the GitHub raw URL of the javstreamio repo. Each title's own stream file carries its
// magnet, so the app never loads the whole catalog at once.

import bundledTitlesJson from "../data/titles.json" with { type: "json" };
import { ADDON_ID, ADDON_NAME, ADDON_VERSION } from "./version.js";
import {
  mapTitleRow,
  openTitlesDb,
  queryCatalog,
  queryTitle,
  resolveSqlitePath,
  searchText,
  searchTokens,
  querySeriesList,
  querySeriesMeta,
  queryEpisodeByRef,
} from "./titles-db.js";

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
    // Movies (single video) point the default video at their own id. Series
    // manage their default episode via querySeriesMeta — do not override.
    if (!hints.defaultVideoId && meta.type !== "series") hints.defaultVideoId = meta.id;
    if (hints.hasScheduledVideos == null) hints.hasScheduledVideos = false;
    meta.behaviorHints = hints;
  }
  return { meta: withStremioCredits(meta) };
}

const POSTER_HOSTS = [
  "tu.djhdhs.us",
  // Sehuatang forum-preview mirror domain (sehuatang forum images rotate
  // across tu.djhdhs.us / tu.w4kmzkm.com); the /poster proxy fetches with
  // the sehuatang.org Referer, so mirror hosts belong on the allowlist.
  "tu.w4kmzkm.com",
  "jdbstatic.com",
  "hanime-cdn.com",
  "raw.githubusercontent.com",
  // HK Cat III posters scraped from douban (subject_suggest -> img*.doubanio.com).
  "doubanio.com",
  "img.douban.com",
  // HK Cat III SVG title cards are served from the live host's /posters dir.
  "javstremio.ddns.net",
  // HK Cat III wiki posters (og:image) live on Wikimedia CDN.
  "upload.wikimedia.org",
  // AvGood detail-page posters (avgood.com/remote/pic/...) are publicly
  // fetchable — HK Cat III magnets + posters come from AvGood search.
  "avgood.com",
  // JAV fourhoi covers: the /poster proxy fetches them server-side with
  // browser UA and NO Referer (any Referer → 403 from fourhoi.com).
  "fourhoi.com",
  // Jacket right-half crops are fetched server-side (DMM pl + MGStage pb_e).
  "pics.dmm.co.jp",
  "image.mgstage.com",
  // HK Cat III poster fill (hk_cat3_posters.py): movie-DB og:image CDNs
  // (TMDB rawHtml og:image, IMDB media CDN).
  "media.themoviedb.org",
  "m.media-amazon.com",
];
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

// These labels have no usable DMM digital poster. Crop the DVD jacket even
// when the row is not flagged dmm_missing (DLDSS's stored poster is a tiny
// mono ps; ABP's is the old centre crop).
const FORCE_JACKET = new Set([
  "JUKF", "SILK", "MEKO", "HODV", "SKMJ", "SDNM", "RCTD", "ABF",
  "NACT", "DRPT", "HMGL", "GVG", "YUME", "YSN", "YAG", "WFS",
  "MILK", "ORG", "AIAV",
  "DLDSS", "HEZ", "ABP", "DPHN", "AWAW", "FSDSS", "AWT", "SERO",
  "KNAM", "HZGD", "CHUC", "HDKA", "IENF", "MGOLD", "NMSL",
]);

// Retail mono CID maker token, from sehuatang's DMM preview URL.
// fourhoi has no cover for several of these.
const MONO_MAKER = {
  ORG: "h_771",
  HDKA: "h_237",
  SERO: "h_422",
  ABP: "118",
};

export function jacketCodePrefix(idOrTitle) {
  const raw = String(idOrTitle || "").replace(/^jav:/i, "").trim();
  const match = raw.match(/^([A-Za-z]{2,})[-_]/);
  return match ? match[1].toUpperCase() : "";
}

export function monoMakerPoster(idOrTitle, size = "pl") {
  const raw = String(idOrTitle || "").replace(/^jav:/i, "").trim();
  const match = raw.match(/^([A-Za-z]{2,})-(\d+)$/);
  if (!match) return "";
  const maker = MONO_MAKER[match[1].toUpperCase()];
  if (!maker) return "";
  const cid = `${maker}${match[1].toLowerCase()}${match[2]}`;
  const suffix = size === "ps" ? "ps" : "pl";
  return `https://pics.dmm.co.jp/mono/movie/adult/${cid}/${cid}${suffix}.jpg`;
}

export function needsJacketCrop(item) {
  const id = String((item && item.id) || "");
  if (!/^jav:[A-Za-z]{2,}-?\d+$/i.test(id)) return false;
  if (item && item.dmmMissing) return true;
  if (/\/posters\/jav(?:censored|uncensored)\//i.test(String((item && item.poster) || ""))) return true;
  return FORCE_JACKET.has(jacketCodePrefix(id));
}

export function jacketArtSources(item) {
  if (!item) return [];
  const urls = [];
  const push = (url) => {
    if (url && !urls.includes(url)) urls.push(url);
  };
  if (!item.fourhoiMissing) push(fourhoiPoster(item.id));
  push(mgstagePoster(item.id));
  push(monoMakerPoster(item.id, "pl"));
  push(dmmMonoPoster(item.id, "pl"));
  if (item.forumPoster) push(item.forumPoster);
  push(dmmPoster(item.id, "pl"));
  return urls;
}

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
      host === "hanime-cdn.com" ||
      host === "fourhoi.com" ||
      host.endsWith(".fourhoi.com") ||
      host === "image.mgstage.com" ||
      host === "jdbstatic.com" ||
      host.endsWith(".jdbstatic.com") ||
      host === "avgood.com" ||
      host === "images.ijavtorrent.com" ||
      host === "raw.githubusercontent.com" ||
      // Our own public art: hnt SVG title cards served from the live host.
      host === "javstremio.ddns.net" ||
      // hnt covers scraped from Sukebei view pages (og:image on oceanveil).
      host === "image.oceanveil.net" ||
      // Series posters scraped from hentai.tv (uploads host is public).
      host === "hentai.tv"
      // HK Cat III posters: douban poster images (hotlink-protected, so
      // catalog/meta responses route them through the /poster proxy).
      || host === "doubanio.com" || host.endsWith(".doubanio.com")
      || host === "img.douban.com"
      // HK Cat III wiki posters (og:image) live on Wikimedia CDN.
      || host === "upload.wikimedia.org"
    );
  } catch {
    return false;
  }
}

// Hotlink-protected image hosts: the /poster proxy must fetch these with a
// host-specific Referer (douban hosts need movie.douban.com; djhdhs.us needs
// sehuatang.org). Any other public host (DMM, fourhoi, hnt cards) fetches
// fine with the default referer.
const NEEDS_PROXY_HOSTS = ["doubanio.com", "img.douban.com"];

function artHost(url) {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch {
    return "";
  }
}

// DMM digital/mono art lives on pics.dmm.co.jp. When the row is flagged
// dmm_missing (the digital CID is a NOW PRINTING stub), the stored DMM
// poster/background must be skipped so the MGStage / fourhoi / forum
// fallbacks are used instead of the gray "NOW PRINTING" placeholder.
function isDmmArtUrl(url) {
  return artHost(url) === "pics.dmm.co.jp";
}

function forumPosterUrl(item, origin) {
  if (!origin || !item || !item.id) return "";
  const host = artHost(item.poster);
  if (
    !item.forumPoster &&
    !String(item.poster || "").includes("djhdhs.us") &&
    !NEEDS_PROXY_HOSTS.includes(host)
  ) return "";
  return `${origin}${posterPathFor(item.type || "movie", item.id)}`;
}

function applyPublicArt(item, kind, origin) {
  if (!item) return item;
  const next = { ...item };
  const skipDmm = Boolean(item.dmmMissing);
  // NOW PRINTING rows, centre-crop stand-ins, and the labels whose only art
  // is the wide DVD jacket. Poster is the right half; fanart stays the full scan.
  if (origin && needsJacketCrop(item)) {
    const crop = `${origin}${posterPathFor(item.type || "movie", item.id)}`;
    next.poster = crop;
    if (kind !== "catalog") next.background = `${crop}?fanart=1`;
    else delete next.background;
    delete next.forumPoster;
    delete next.dmmMissing;
    delete next.fourhoiMissing;
    return next;
  }
  const ps = skipDmm ? "" : dmmPoster(item.id, "ps");
  const pl = skipDmm ? "" : dmmPoster(item.id, "pl");
  // fourhoi.com 403s any request carrying a Referer, and the Stremio
  // client's image fetches send one, so the cover is proxied through
  // /poster (server-side fetch sends no Referer → 200). Callers without
  // an origin (unit tests) keep the direct fourhoi URL.
  const fourhoiDirect = fourhoiPoster(item.id);
  const fourhoi = item.fourhoiMissing || !fourhoiDirect
    ? ""
    : origin
      ? `${origin}${posterPathFor(item.type || "movie", item.id)}`
      : fourhoiDirect;
  const mgstage = mgstagePoster(item.id);
  const monoPs = skipDmm ? "" : dmmMonoPoster(item.id, "ps");
  const monoPl = skipDmm ? "" : dmmMonoPoster(item.id, "pl");
  const proxied = forumPosterUrl(item, origin);
  // A DB-stored DMM poster/background is only usable while the digital
  // CID is not a NOW PRINTING stub (dmm_missing=1).
  const useStoredPoster = isPublicArtUrl(item.poster) && !(skipDmm && isDmmArtUrl(item.poster));
  const useStoredBg = isPublicArtUrl(item.background) && !(skipDmm && isDmmArtUrl(item.background));
  if (kind === "catalog") {
    if (useStoredPoster) next.poster = item.poster;
    else if (ps) next.poster = ps;
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
  if (useStoredPoster) next.poster = item.poster;
  else if (ps) next.poster = ps;
  else if (mgstage) next.poster = mgstage;
  else if (monoPs) next.poster = monoPs;
  else if (fourhoi) next.poster = fourhoi;
  else if (proxied) next.poster = proxied;
  else if (!isPublicArtUrl(next.poster)) delete next.poster;
  if (useStoredBg) next.background = item.background;
  else if (pl) next.background = pl;
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

// JAV: DB-stored poster/background (DMM mono / MGStage / fourhoi, probed
// at build time) wins; generated DMM digital URLs are only a fallback when
// the DB row has no public poster. When dmm_missing (NOW PRINTING stub):
// skip all DMM (digital + mono) → MGStage, FourHoi, then sehuatang /poster.
// Western/Others use sehuatang through /poster.
export function withPublicPosters(body, kind, origin = "") {
  if (!body || typeof body !== "object") return body;
  if (kind === "catalog" && Array.isArray(body.metas)) {
    return { ...body, metas: body.metas.map((item) => applyPublicArt(item, "catalog", origin)) };
  }
  if (kind === "meta" && body.meta) {
    const meta = applyPublicArt(body.meta, "meta", origin);
    if (Array.isArray(meta.videos)) {
      // Series meta: apply public-art rules to each video; keep the
      // thumbnail only when its host is on the public allowlist.
      meta.videos = meta.videos.map((v) => {
        const nv = applyPublicArt(v, "meta", origin);
        if (nv.thumbnail && !isPublicArtUrl(nv.thumbnail)) delete nv.thumbnail;
        return nv;
      });
    }
    return { ...body, meta };
  }
  return body;
}

// Filter-chip options computed from the live titles.sqlite (top-10 genres
// per JAV category, every distinct release year). The Stremio client only
// renders a genre filter when the catalog carries an `options` list, and
// the bare `year` extra name is not recognized by the client, so year
// filtering is provided by dedicated "-year" catalogs following the
// Cinemeta pattern: the client sends the selected year through the
// `genre` param, which the catalog handler treats as a year filter.
const JAV_CENSORED_GENRES = [
  "單體作品", "中出", "巨乳", "已婚婦女", "熟女",
  "白天出軌", "蕩婦", "苗條", "口交", "戲劇",
  "多P", "美少女電影", "無碼破解", "淫亂真實", "乳交",
  "潮吹", "女上位", "出軌", "凌辱", "接吻",
  "美乳", "數位馬賽克", "亂倫", "4K", "姐姐",
  "顏射", "美少女", "高中女生", "制服", "紀錄片",
];
const JAV_UNCENSORED_GENRES = [
  "單體作品", "無碼破解", "中出", "巨乳", "已婚婦女",
  "苗條", "蕩婦", "白天出軌", "熟女", "口交",
  "多P", "戲劇", "淫亂真實", "美少女電影", "數位馬賽克",
  "女上位", "顏射", "潮吹", "接吻", "乳交",
  "姐姐", "4K", "凌辱", "美少女", "出軌",
  "主觀視角", "OL", "美乳", "連褲襪", "制服",
];
const JAV_CENSORED_YEARS = [
  "2026", "2025", "2024", "2023", "2022", "2021", "2020", "2019",
  "2018", "2017", "2016", "2015", "2014", "2013", "2012", "2011",
  "2010", "2009", "2008", "2007", "2006", "2005",
];
const JAV_UNCENSORED_YEARS = [
  "2026", "2025", "2024", "2023", "2022", "2021", "2020", "2019",
  "2018", "2017", "2016", "2015", "2014", "2013", "2012", "2011",
  "2010", "2009", "2008", "2007", "2006", "2003",
];

const CATALOG_EXTRA = [
  { name: "search", isRequired: false },
  { name: "skip", isRequired: false },
  { name: "genre", isRequired: false },
  { name: "year", isRequired: false },
];

export function catalogExtra(catalogId) {
  const id = String(catalogId || "").replace(/\.json$/, "");
  if (id === "jav-censored-year") {
    return [
      { name: "genre", isRequired: true, options: JAV_CENSORED_YEARS, optionsLimit: 1 },
      { name: "skip", isRequired: false },
    ];
  }
  if (id === "jav-uncensored-year") {
    return [
      { name: "genre", isRequired: true, options: JAV_UNCENSORED_YEARS, optionsLimit: 1 },
      { name: "skip", isRequired: false },
    ];
  }
  const extra = [
    { name: "search", isRequired: false },
    { name: "skip", isRequired: false },
  ];
  if (id === "jav-censored") {
    extra.push({ name: "genre", isRequired: false, options: JAV_CENSORED_GENRES, optionsLimit: 1 });
  } else if (id === "jav-uncensored") {
    extra.push({ name: "genre", isRequired: false, options: JAV_UNCENSORED_GENRES, optionsLimit: 1 });
  } else {
    extra.push({ name: "genre", isRequired: false });
    extra.push({ name: "year", isRequired: false });
  }
  return extra;
}

export function catalogGenres(catalogId) {
  const id = String(catalogId || "").replace(/\.json$/, "");
  switch (id) {
    case "jav-censored": return JAV_CENSORED_GENRES;
    case "jav-uncensored": return JAV_UNCENSORED_GENRES;
    case "jav-censored-year": return JAV_CENSORED_YEARS;
    case "jav-uncensored-year": return JAV_UNCENSORED_YEARS;
    default: return undefined;
  }
}

export const BUNDLED_CATALOGS = [
  { type: "movie", id: "jav-censored", name: "JAV Censored", genres: JAV_CENSORED_GENRES, extra: catalogExtra("jav-censored") },
  // Cinemeta-style year catalog: the genre extra's options are years; the
  // client sends the selected year via the `genre` param.
  {
    type: "movie",
    id: "jav-censored-year",
    name: "JAV Censored By Year",
    genres: JAV_CENSORED_YEARS,
    extra: catalogExtra("jav-censored-year"),
    extraSupported: ["genre", "skip"],
    extraRequired: ["genre"],
  },
  { type: "movie", id: "jav-uncensored", name: "JAV Uncensored", genres: JAV_UNCENSORED_GENRES, extra: catalogExtra("jav-uncensored") },
  {
    type: "movie",
    id: "jav-uncensored-year",
    name: "JAV Uncensored By Year",
    genres: JAV_UNCENSORED_YEARS,
    extra: catalogExtra("jav-uncensored-year"),
    extraSupported: ["genre", "skip"],
    extraRequired: ["genre"],
  },
  { type: "movie", id: "western", name: "Western", extra: CATALOG_EXTRA },
  { type: "movie", id: "others", name: "Others", extra: CATALOG_EXTRA },
  // Single Hentai catalog (hentai.tv source; replaces the censored/uncensored pair).
  { type: "series", id: "hentai", name: "Hentai", extra: CATALOG_EXTRA },
  // HK Category III (Wikipedia meta + SearXNG magnet search; Cantonese original audio priority).
  { type: "movie", id: "hongkong", name: "HK Cat III", extra: CATALOG_EXTRA },
  // sehuatang forum-107 per-country split (2026-11-15): each row is tagged
  // by subcategory; 'western' above already serves JAV western + hk US 三级/四级.
  { type: "movie", id: "korea", name: "Korea Cat III", extra: CATALOG_EXTRA },
  { type: "movie", id: "japan", name: "Japan Cat III", extra: CATALOG_EXTRA },
  { type: "movie", id: "france", name: "France Cat III", extra: CATALOG_EXTRA },
  { type: "movie", id: "japan-photo", name: "Japan Photo", extra: CATALOG_EXTRA },
  { type: "movie", id: "cat3-others", name: "Cat III Others", extra: CATALOG_EXTRA },
  // korean, chinese stay paused while their metadata sources are
  // re-vetted; re-add them here + in titles-db PAID_ONLY_CATEGORIES when redone.
];

const CATALOG_PAGE = 80;

export function parseCatalogExtra(extra) {
  const raw = String(extra || "").replace(/\.json$/i, "");
  const params = new URLSearchParams(raw);
  const skip = Number(params.get("skip") || 0);
  return {
    search: (params.get("search") || "").trim(),
    skip: Number.isFinite(skip) && skip > 0 ? skip : 0,
    genre: (params.get("genre") || "").trim(),
    year: (params.get("year") || "").trim(),
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
  // Year catalogs are views over their base category (Cinemeta pattern):
  // the year filter is applied by the catalog handler via the genre param.
  if (id === "jav-censored-year") return "jav-censored";
  if (id === "jav-uncensored-year") return "jav-uncensored";
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

export function bundledCatalog(catalogId, skip = 0, page = CATALOG_PAGE, search = "", genre = "", year = "") {
  const key = resolveCatalogId(catalogId);
  const rawId = String(catalogId || "").replace(/\.json$/, "");
  const genreParam = String(genre || "").trim();
  const yearParam = String(year || "").trim();
  // Cinemeta pattern: a year catalog sends the selected year through the
  // `genre` extra, so treat a 4-digit genre value as a year filter.
  const isYearCatalog = rawId === "jav-censored-year" || rawId === "jav-uncensored-year";
  const treatGenreAsYear =
    isYearCatalog ||
    ((key === "jav-censored" || key === "jav-uncensored") && /^\d{4}$/.test(genreParam));
  const qGenre = treatGenreAsYear ? "" : genreParam;
  const qYear = treatGenreAsYear ? yearParam || genreParam : yearParam;
  const db = getTitlesDb();
  // hnt categories are series: one catalog entry per episode collection.
  if (db && key.startsWith("hentai")) {
    // skip is pushed into SQL OFFSET (the old .slice on the first `limit`
    // rows made every page after the first 80 series empty).
    const series = querySeriesList(db, { category: key, limit: page, offset: skip, search, genre: qGenre, year: qYear });
    return {
      metas: series.map((s) => ({
        id: `hnt:${s.seriesKey}`,
        type: "series",
        name: s.name,
        description: s.description || "",
        ...(s.poster ? { poster: s.poster } : {}),
        ...(s.epCount ? { episodes: s.epCount } : {}),
      })),
    };
  }
  const genreMatch = (item) =>
    !qGenre || (item.genres || []).some((g) => String(g).toLowerCase() === qGenre.toLowerCase());
  const yearMatch = (item) =>
    !qYear ||
    String(item.releaseInfo || "").includes(qYear) ||
    String(item.released || "").startsWith(qYear);
  const list = db
    ? queryCatalog(db, { catalogId: key, skip, page, search, genre: qGenre, year: qYear })
    : (loadBundledTitles()[key] || [])
      .filter((item) => titleMatchesSearch(item, search))
      .filter(genreMatch)
      .filter(yearMatch)
      .slice(skip, skip + page);
  return {
    metas: list.map((item) => {
      const preview = {
        id: item.id,
        type: "movie",
        name: item.name,
        releaseInfo: item.releaseInfo || "",
      };
      // RFC3339 release date (stremio-core parses meta.released as DateTime).
      if (item.released) preview.released = item.released;
      if (item.poster) preview.poster = item.poster;
      if (item.forumPoster) preview.forumPoster = item.forumPoster;
      if (item.dmmMissing) preview.dmmMissing = true;
      if (item.fourhoiMissing) preview.fourhoiMissing = true;
      return preview;
    }),
  };
}

// hnt refs: "hnt:<key>:<n>" = episode, "hnt:<key>" = series (a slug,
// distinct from the row id "hnt:nyaa-<id>").
function parseHntEpisodeRef(id) {
  const m = String(id || "").replace(/\.json$/, "").match(/^hnt:([^:]+):(\d+)$/);
  return m ? { key: m[1], episode: Number(m[2]) } : null;
}

export function bundledMeta(id) {
  const db = getTitlesDb();
  const epRef = parseHntEpisodeRef(id);
  if (db && epRef) {
    const row = queryEpisodeByRef(db, String(id).replace(/\.json$/, ""));
    if (!row) return null;
    const meta = {
      id: `hnt:${epRef.key}:${epRef.episode}`,
      type: "episode",
      name: `Episode ${epRef.episode}`,
      // Synopsis only — brand/quality/rating/year/language lines live in the
      // dedicated fields (imdbRating, released), not in the summary.
      description: row.description || "",
      poster: row.poster || "",
      runtime: row.runtime || "",
      imdbRating: row.imdb_rating || "",
      // normalizeMetaBody validates/converts it to strict RFC3339 (or drops it).
      ...(row.released ? { released: row.released } : {}),
    };
    if (row.stream_url) {
      meta.behaviorHints = { bingeGroup: `javstreamio-online-${row.name || "x"}` };
    }
    return { meta: withStremioCredits(meta) };
  }
  const rawId = String(id || "").replace(/\.json$/, "");
  const seriesKey = rawId.startsWith("hnt:") && !/^hnt:nyaa-\d+$/.test(rawId)
    ? rawId.slice("hnt:".length)
    : "";
  if (db && seriesKey) {
    const series = querySeriesMeta(db, seriesKey);
    if (series) {
      const meta = {
        id: series.id,
        type: "series",
        name: series.name,
        description: series.description || "",
        poster: series.poster || "",
        background: series.background || "",
        imdbRating: series.imdbRating || "",
        // Series release date (RFC3339) — stremio-core deserializes Meta.released.
        ...(series.released ? { released: series.released } : {}),
        genres: series.genres || [],
        // stremio-core reads `videos`, not `episodes` — keep the key the
        // client actually deserializes.
        videos: (series.videos || []).map((v) => ({ ...v })),
        // Pass through the hints computed by querySeriesMeta: solo (single-episode)
        // series carry defaultVideoId (so Stremio plays the lone episode);
        // multi-episode series omit it so the client renders the episode grid
        // (all episodes + posters) instead of auto-opening S1E1.
        behaviorHints: { ...(series.behaviorHints || {}), hasScheduledVideos: false },
      };
      // Series-level behavior hints: bingeGroup per infoHash keeps episodes
      // grouped in the Stremio player's "up next" (AGENTS.md convention).
      return { meta: withStremioCredits(meta) };
    }
  }
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
  const epRef = parseHntEpisodeRef(id);
  const db = getTitlesDb();
  let item = null;
  if (db && epRef) {
    const row = queryEpisodeByRef(db, String(id).replace(/\.json$/, ""));
    if (row) item = mapTitleRow(row);
  }
  if (!item) item = bundledTitle(id);
  if (!item) return null;
  const streams = [];
  if (item.infoHash) {
    streams.push({
      name: item.runtime || "",
      infoHash: item.infoHash,
      magnet: item.magnet || "",
      description: item.name || "",
    });
  }
  if (item.streamUrl && !epRef) {
    // Direct online playback: a plain http(s) mp4 the player can stream
    // without a debrid key. Hnt hentaistream mp4s (r2.1hanime.com) are
    // Cloudflare-protected (403 without clearance), so hnt episodes rely
    // on the debrid (e.g. PikPak) and P2P infoHash rows instead.
    streams.push({
      name: "Online",
      url: item.streamUrl,
      description: item.name || "",
      behaviorHints: { bingeGroup: `javstreamio-online-${item.name || "x"}` },
    });
  }
  if (!streams.length) return null;
  return { streams };
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

// doubanio.com / img.douban.com are hotlink-protected: they 418/403 without
// a douban.com Referer. All other poster hosts use the default referer.
function posterRefererFor(src) {
  try {
    const host = new URL(src).hostname.toLowerCase();
    if (
      host === "img.douban.com" || host === "douban.com" || host.endsWith(".douban.com") ||
      host === "doubanio.com" || host.endsWith(".doubanio.com")
    ) {
      return "https://movie.douban.com/";
    }
    // fourhoi.com 403s any request carrying a Referer (no Referer → 200).
    // Fetch the cover with browser UA and no Referer header at all.
    if (host === "fourhoi.com" || host.endsWith(".fourhoi.com")) {
      return "";
    }
    // DMM and MGStage jackets 200 with no Referer. A sehuatang Referer is
    // not required and can 403 the image.
    if (host === "pics.dmm.co.jp" || host === "image.mgstage.com") {
      return "";
    }
  } catch {
    return POSTER_REFERER;
  }
  return POSTER_REFERER;
}

export async function fetchRemotePoster(src, allowAnyHttps = false) {
  if (!allowAnyHttps && !isAllowedPosterUrl(src)) {
    const err = new Error("Poster host not allowed");
    err.status = 400;
    throw err;
  }
  const referer = posterRefererFor(src);
  const headers = {
    "User-Agent": POSTER_UA,
    Accept: "image/jpeg,image/png,image/*;q=0.5",
  };
  // Omit the Referer header entirely for hosts that 403 on any Referer
  // (fourhoi.com) instead of sending an empty one.
  if (referer) headers.Referer = referer;
  const res = await fetch(src, { headers, redirect: "follow" });
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

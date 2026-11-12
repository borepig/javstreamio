import { DatabaseSync } from "node:sqlite";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CANDIDATES = [
  path.join(HERE, "../data/titles.sqlite"),
  path.join(process.cwd(), "data", "titles.sqlite"),
];

export function resolveSqlitePath() {
  return CANDIDATES.find((file) => existsSync(file)) || "";
}

export const DEFAULT_SQLITE = resolveSqlitePath() || CANDIDATES[0];

export function searchText({ id, name, cast, genres } = {}) {
  const parts = [id, name, ...(cast || []), ...(genres || [])].filter(Boolean).map(String);
  const joined = parts.join(" ").toLowerCase();
  const hyphenless = joined.replace(/-/g, "");
  const compact = joined.replace(/[^a-z0-9\u3040-\u30ff\u4e00-\u9fff]+/g, "");
  return `${joined} ${hyphenless} ${compact}`.trim();
}

export function searchTokens(query) {
  return String(query || "")
    .trim()
    .toLowerCase()
    .replace(/[-_]+/g, " ")
    .split(/\s+/)
    .map((token) => token.replace(/%/g, ""))
    .filter(Boolean);
}

export function openTitlesDb(source = DEFAULT_SQLITE) {
  if (source && typeof source === "object" && typeof source.prepare === "function") {
    return source;
  }
  const file = String(source || DEFAULT_SQLITE);
  const db = new DatabaseSync(file, { readOnly: true });
  const cols = db.prepare("PRAGMA table_info(titles)").all().map((c) => c.name);
  if (cols.includes("meta_rank")) {
    META_RANK = "meta_rank";
  }
  return db;
}

// Idempotent schema upgrade: add the precomputed `meta_rank` column (1 = bare
// row, 0 = enriched row) and the catalog index so queryCatalog / isInFreeSet
// are served from the index instead of a full-table scan.
export function migrateTitlesDb(file) {
  const db = new DatabaseSync(String(file || DEFAULT_SQLITE));
  const cols = db.prepare("PRAGMA table_info(titles)").all().map((c) => c.name);
  if (!cols.includes("meta_rank")) {
    db.exec("ALTER TABLE titles ADD COLUMN meta_rank INTEGER DEFAULT 0");
    db.exec(
      `UPDATE titles SET meta_rank = 1
       WHERE (genres_json IS NULL OR genres_json IN ('', '[]'))
          AND (description IS NULL OR description = '')`,
    );
  }
  db.exec("CREATE INDEX IF NOT EXISTS idx_titles_cat_rank ON titles (category, meta_rank, posted_at, id)");
  // hnt series queries filter/join on (category, series_key, episode_num) —
  // without it the hnt series catalog page took ~14s (correlated subqueries).
  if (cols.includes("series_key") && cols.includes("episode_num")) {
    db.exec("CREATE INDEX IF NOT EXISTS idx_titles_cat_series ON titles (category, series_key, episode_num)");
  }
  db.close();
}

function parseJsonArray(raw) {
  try {
    const value = JSON.parse(raw || "[]");
    return Array.isArray(value) ? value.filter(Boolean) : [];
  } catch {
    return [];
  }
}

// hnt rows are Sukebei/Nyaa hentai collections: served as Stremio *series*
// (TV-show style, one series per collection with an episode list), while
// every other category stays "movie".
export function titleType(category) {
  return String(category || "").startsWith("hentai") ? "series" : "movie";
}

export function mapTitleRow(row) {
  if (!row) return null;
  const item = {
    id: row.id,
    type: titleType(row.category),
    name: row.name,
    description: row.description || "",
    releaseInfo: row.release_info || "",
    runtime: row.runtime || "",
    infoHash: row.info_hash || "",
    magnet: row.magnet || "",
    category: row.category || "",
  };
  if (row.stream_url) item.streamUrl = row.stream_url;
  if (row.lulu_token) item.luluToken = row.lulu_token;
  if (row.released) item.released = row.released;
  if (row.poster) item.poster = row.poster;
  if (row.background) item.background = row.background;
  if (row.forum_poster) item.forumPoster = row.forum_poster;
  if (row.dmm_missing) item.dmmMissing = true;
  if (row.fourhoi_missing) item.fourhoiMissing = true;
  const cast = parseJsonArray(row.cast_json);
  if (cast.length) item.cast = cast;
  const genres = parseJsonArray(row.genres_json);
  if (genres.length) item.genres = genres;
  if (row.director) item.director = row.director;
  if (row.imdb_rating) item.imdbRating = row.imdb_rating;
  return item;
}

// Rows with metadata (non-empty genres or description) rank 0 (front);
// bare rows rank 1 (back). Within a rank, newest first. When the live DB
// has the precomputed `meta_rank` column (added by scripts/migrate-meta-rank.mjs),
// ORDER BY can be served from the (category, meta_rank, posted_at, id) index
// instead of scanning every row with the CASE expression (34k+ rows = ~3s/page
// on the t3.micro).
let META_RANK = `CASE WHEN (genres_json IS NULL OR genres_json IN ('', '[]')) AND (description IS NULL OR description = '') THEN 1 ELSE 0 END`;

export function queryCatalog(db, { catalogId, skip = 0, page = 80, search = "" } = {}) {
  const category = String(catalogId || "").replace(/\.json$/, "");
  const tokens = searchTokens(search);
  let sql = "SELECT * FROM titles WHERE category = ?";
  const params = [category];
  for (const token of tokens) {
    sql += " AND search_text LIKE ?";
    params.push(`%${token}%`);
  }
  sql += ` ORDER BY ${META_RANK}, posted_at DESC, id DESC LIMIT ? OFFSET ?`;
  params.push(Number(page) || 80, Number(skip) || 0);
  return db.prepare(sql).all(...params).map(mapTitleRow);
}

export function queryTitle(db, id) {
  const want = decodeURIComponent(String(id || "").replace(/\.json$/, ""));
  const row = db.prepare("SELECT * FROM titles WHERE id = ?").get(want);
  return mapTitleRow(row);
}

// Free tier size: the newest N titles per category. Paid tier gets the full
// catalog/meta; free users only ever see this capped set, so the free meta
// view can't leak paid titles.
export const FREE_CATALOG_SIZE = 15;

// Paid-only categories: never part of the free tier — free users get an
// empty catalog and 404 meta for these.
export const PAID_ONLY_CATEGORIES = [
  "hentai",
  "hongkong",
  // korean, chinese stay paused: re-add their names here when
  // their metadata sources are vetted and redone.
];

// Is this title inside the free (newest FREE_CATALOG_SIZE per category) set?
// Rank = number of same-category titles strictly ahead of it in the
// meta-first ordering used by queryCatalog (META_RANK, posted_at DESC, id DESC).
export function isInFreeSet(db, id) {
  const want = decodeURIComponent(String(id || "").replace(/\.json$/, ""));
  const row = db.prepare(`SELECT category, posted_at, id, ${META_RANK} AS rank FROM titles WHERE id = ?`).get(want);
  if (!row) return false;
  if (PAID_ONLY_CATEGORIES.includes(row.category)) return false;
  const { n } = db
    .prepare(
      `SELECT COUNT(*) AS n FROM titles t
       WHERE t.category = ?
         AND (${META_RANK} < ?
              OR (${META_RANK} = ? AND (t.posted_at > ? OR (t.posted_at = ? AND t.id > ?))))`,
    )
    .get(row.category, row.rank, row.rank, row.posted_at, row.posted_at, want);
  return n < FREE_CATALOG_SIZE;
}

// --- hnt series queries ----------------------------------------------------
// series_key / episode_num are filled by addon/group_hentai_series.py.
// Series id: "hnt:<series_key>"; episode id: "hnt:<series_key>:<episode_num>".

// Derive a clean display name from a slugified series_key:
// "my-matchmaking-partner-is-my-student" -> "My Matchmaking Partner Is My Student".
// Quality tokens that survive in the slug (audio/eng/sub/web/dl/...) are
// dropped so a series card doesn't read "… Audio Eng Sub".
const SERIES_NAME_STOP = new Set([
  "audio", "eng", "sub", "subs", "subbed", "dub", "dual", "vostfr", "final",
  "web", "dl", "hd", "h264", "h265", "avc", "x264", "aac", "flac",
  "1080p", "720p", "480p", "ov", "ova",
]);

export function seriesDisplayName(seriesKey) {
  const key = String(seriesKey || "").trim();
  if (!key) return "";
  return key
    .split("-")
    .filter((w) => w && !SERIES_NAME_STOP.has(w.toLowerCase()))
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export function querySeriesList(db, { category, limit = 80, offset = 0, search = "" } = {}) {
  const cat = String(category || "").replace(/\.json$/, "");
  const limitN = Number(limit) || 80;
  const offsetN = Number(offset) || 0;
  const searching = String(search || "").trim() !== "";
  const tokens = searchTokens(search);
  // Two-stage lookup: the old single query ran 3 correlated subqueries per
  // group (no index on series_key) -> ~14s per catalog page on the t3.micro.
  // Stage 1: series rows (cheap GROUP BY). Stage 2: one indexed IN() query
  // for the details of the selected series, aggregated in JS.
  // Without a search, SQL does the pagination (LIMIT/OFFSET). With a search,
  // the SQL must return EVERY series (no LIMIT) so the filter runs over the
  // full set; the page is then sliced in JS. Searching only the first page
  // (the old behavior) made deep series (e.g. Sister Breeder, ranked ~1568/1569)
  // invisible to search.
  let sql = `SELECT t.series_key AS series_key,
               COUNT(*) AS ep_count,
               MAX(t.posted_at) AS posted_at
       FROM titles t
       WHERE t.category = ? AND t.series_key != ''
       GROUP BY t.series_key
       ORDER BY posted_at DESC, t.series_key ASC`;
  const params = [cat];
  if (!searching) {
    sql += "\n       LIMIT ? OFFSET ?";
    params.push(limitN, offsetN);
  }
  const seriesRows = db.prepare(sql).all(...params);
  if (!seriesRows.length) return [];
  // Search the full set by slug-derived name + id, then fetch details only
  // for the matched keys (keeps the detail IN() list small and under the
  // SQLite variable limit).
  let keys;
  if (searching) {
    const matched = [];
    for (const r of seriesRows) {
      const derived = r.series_key.startsWith("solo-") ? "" : seriesDisplayName(r.series_key);
      const name = derived || `hnt:${r.series_key}`;
      const hay = searchText({ id: `hnt:${r.seriesKey}`, name }).toLowerCase();
      if (tokens.length === 0 || tokens.every((t) => hay.includes(t))) {
        matched.push(r.series_key);
      }
    }
    if (!matched.length) return [];
    keys = matched;
  } else {
    keys = seriesRows.map((r) => r.series_key);
  }
  const ph = keys.map(() => "?").join(", ");
  const detail = db
    .prepare(
      `SELECT series_key, name, poster, description, episode_num
       FROM titles
       WHERE category = ? AND series_key IN (${ph})`,
    )
    .all(cat, ...keys);
  detail.sort((a, b) => (Number(a.episode_num) || 0) - (Number(b.episode_num) || 0));
  const byKey = new Map();
  for (const row of detail) {
    const agg = byKey.get(row.series_key) || { name: "", poster: "", description: "" };
    const name = String(row.name || "").trim();
    if (name.length > agg.name.length) agg.name = name;
    if (row.poster && !agg.poster) agg.poster = String(row.poster).trim();
    const desc = String(row.description || "").trim();
    if (desc.length > agg.description.length) agg.description = desc;
    byKey.set(row.series_key, agg);
  }
  const matchedSet = searching
    ? new Set(keys)
    : null;
  const page = seriesRows
    .filter((r) => !matchedSet || matchedSet.has(r.series_key))
    .map((r) => {
      const agg = byKey.get(r.series_key) || {};
      const derived = r.series_key.startsWith("solo-") ? "" : seriesDisplayName(r.series_key);
      return {
        seriesKey: r.series_key,
        // Clean display name from the slug; solo series keep their raw name.
        name: derived || agg.name || `hnt:${r.series_key}`,
        poster: agg.poster || "",
        description: agg.description || "",
        epCount: Number(r.ep_count) || 0,
      };
    });
  // When searching, the SQL returned all rows (no LIMIT) — slice the page in JS.
  return searching ? page.slice(offsetN, offsetN + limitN) : page;
}

// Video.released must be RFC3339 (stremio-core parses it as a DateTime);
// date-only strings would fail deserialization and drop the whole meta.
const RFC3339_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/;

export function querySeriesMeta(db, seriesKey) {
  const key = String(seriesKey || "").replace(/\.json$/, "");
  if (!key) return null;
  const rows = db
    .prepare(
      `SELECT id, name, description, poster, background, runtime, info_hash, stream_url, episode_num,
               release_info, imdb_rating, genres_json, released
       FROM titles WHERE series_key = ? AND category LIKE 'hentai%'
       ORDER BY episode_num ASC`
    )
    .all(key);
  if (!rows.length) return null;
  // Series name: a clean display name derived from the slug (hyphens ->
  // spaces, quality tokens dropped). Solo series (single episode) keep the
  // longest raw row name, which is more informative than "Solo 4742254".
  const longestRowName = rows.map((r) => r.name || "").sort((a, b) => b.length - a.length)[0];
  const name = key.startsWith("solo-")
    ? longestRowName || `hnt:${key}`
    : seriesDisplayName(key) || longestRowName || `hnt:${key}`;
  const poster = rows.find((r) => r.poster)?.poster || "";
  const background = rows.find((r) => r.background)?.background || poster;
  // The series row (episode_num = 0) holds series-level fields; its
  // description was copied from the episode 1 page by the scraper. Old hnt
  // series have no series row — fall back to the longest row description.
  const hasSeriesRow = rows.some((r) => Number(r.episode_num) === 0);
  const seriesRow = rows.find((r) => Number(r.episode_num) === 0) || rows[0];
  const longestDesc = rows.map((r) => r.description || "").sort((a, b) => b.length - a.length)[0];
  // Synopsis-only description (user instruction: brand/quality/rating/year/
  // language/release date are NOT summary text — they live in dedicated
  // stremio fields: rating -> imdbRating, release date -> released (RFC3339)).
  const baseDesc = hasSeriesRow ? (seriesRow.description || "") : longestDesc;
  const released = seriesRow?.released && RFC3339_RE.test(seriesRow.released) ? seriesRow.released : "";
  // The series row is not an episode — keep only real episodes (episode_num > 0).
  // stremio-core's MetaItem deserializes `videos` (the `episodes` key is
  // ignored), and each video must use the Video fields: title / thumbnail /
  // overview / season / episode / released (RFC3339).
  const videos = rows
    .filter((r) => Number(r.episode_num) > 0)
    .map((r) => {
      const video = {
        id: `hnt:${key}:${r.episode_num}`,
        type: "episode",
        title: `Episode ${r.episode_num}`,
        // Per-episode poster (the episode's own cover from the hentai.tv
        // episode page), falling back to the Sukebei thumbnail.
        thumbnail: r.poster || "",
        // Per-episode synopsis from the episode page's watch-desc block; the
        // summary changes per episode (user instruction).
        overview: r.description || "",
        season: 1,
        episode: Number(r.episode_num),
        runtime: r.runtime || "",
        genres: parseJsonArray(r.genres_json),
      };
      if (r.released && RFC3339_RE.test(r.released)) video.released = r.released;
      return video;
    });
  return {
    id: `hnt:${key}`,
    type: "series",
    name,
    description: baseDesc,
    poster,
    background,
    ...(released ? { released } : {}),
    imdbRating: seriesRow?.imdb_rating || "",
    genres: parseJsonArray(seriesRow?.genres_json),
    videos,
    behaviorHints: {
      // Solo (single-episode) series point the default video at that episode
      // so Stremio plays it directly. Multi-episode series omit defaultVideoId
      // so the client renders the episode grid (all episodes + posters)
      // instead of auto-opening S1E1.
      ...(videos.length === 1 && videos[0] ? { defaultVideoId: videos[0].id } : {}),
      hasScheduledVideos: false,
    },
  };
}

export function queryEpisodeByRef(db, refId) {
  const m = String(refId || "").replace(/\.json$/, "").match(/^hnt:([^:]+):(\d+)$/);
  if (!m) return null;
  return db
    .prepare("SELECT * FROM titles WHERE series_key = ? AND episode_num = ?")
    .get(m[1], Number(m[2]));
}

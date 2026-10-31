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
  return new DatabaseSync(file, { readOnly: true });
}

function parseJsonArray(raw) {
  try {
    const value = JSON.parse(raw || "[]");
    return Array.isArray(value) ? value.filter(Boolean) : [];
  } catch {
    return [];
  }
}

export function mapTitleRow(row) {
  if (!row) return null;
  const item = {
    id: row.id,
    type: "movie",
    name: row.name,
    description: row.description || "",
    releaseInfo: row.release_info || "",
    runtime: row.runtime || "",
    infoHash: row.info_hash || "",
    magnet: row.magnet || "",
    category: row.category || "",
  };
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

export function queryCatalog(db, { catalogId, skip = 0, page = 80, search = "" } = {}) {
  const category = String(catalogId || "").replace(/\.json$/, "");
  const tokens = searchTokens(search);
  let sql = "SELECT * FROM titles WHERE category = ?";
  const params = [category];
  for (const token of tokens) {
    sql += " AND search_text LIKE ?";
    params.push(`%${token}%`);
  }
  sql += " ORDER BY posted_at DESC, id DESC LIMIT ? OFFSET ?";
  params.push(Number(page) || 80, Number(skip) || 0);
  return db.prepare(sql).all(...params).map(mapTitleRow);
}

export function queryTitle(db, id) {
  const want = decodeURIComponent(String(id || "").replace(/\.json$/, ""));
  const row = db.prepare("SELECT * FROM titles WHERE id = ?").get(want);
  return mapTitleRow(row);
}

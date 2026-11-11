import { test } from "node:test";
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import { FREE_CATALOG_SIZE, isInFreeSet, openTitlesDb, queryCatalog, querySeriesList, querySeriesMeta, queryTitle, searchText, seriesDisplayName } from "./titles-db.js";

function createTitlesSchema(db) {
  db.exec(`
    CREATE TABLE titles (
      id TEXT PRIMARY KEY,
      category TEXT NOT NULL,
      name TEXT NOT NULL,
      description TEXT DEFAULT '',
      release_info TEXT DEFAULT '',
      released TEXT DEFAULT '',
      runtime TEXT DEFAULT '',
      poster TEXT DEFAULT '',
      background TEXT DEFAULT '',
      forum_poster TEXT DEFAULT '',
      dmm_missing INTEGER DEFAULT 0,
      fourhoi_missing INTEGER DEFAULT 0,
      cast_json TEXT DEFAULT '[]',
      genres_json TEXT DEFAULT '[]',
      director TEXT DEFAULT '',
      imdb_rating TEXT DEFAULT '',
      info_hash TEXT DEFAULT '',
      magnet TEXT DEFAULT '',
      posted_at TEXT DEFAULT '',
      search_text TEXT DEFAULT ''
    );
  `);
}

function memoryDb() {
  const db = new DatabaseSync(":memory:");
  createTitlesSchema(db);
  const insert = db.prepare(`
    INSERT INTO titles (id, category, name, description, cast_json, genres_json, info_hash, posted_at, search_text)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);
  // JUR-093: enriched (JavDB meta) — genres present → meta-first group.
  insert.run("jav:JUR-093", "jav-censored", "JUR-093 無名の原石妻", "JavDB meta", '["宝生みさと"]', '["AV","Romance"]', "aaa", "2026-09-23", searchText({
    id: "jav:JUR-093",
    name: "JUR-093 無名の原石妻",
    cast: ["宝生みさと"],
  }));
  // WAAA-629: no genres, no description → back of the catalog.
  insert.run("jav:WAAA-629", "jav-censored", "WAAA-629 something", "", '["田中"]', "[]", "bbb", "2026-08-01", searchText({
    id: "jav:WAAA-629",
    name: "WAAA-629 something",
    cast: ["田中"],
  }));
  // ABF-389: has a description (no genres) → counts as meta.
  insert.run("jav:ABF-389", "jav-censored", "ABF-389 prestige", "Prestige meta", "[]", "[]", "ccc", "2026-07-01", searchText({
    id: "jav:ABF-389",
    name: "ABF-389 prestige",
    cast: [],
  }));
  insert.run("jav:thread-1", "western", "Innocent High", "", '["Cadence Carter"]', "[]", "ddd", "2026-10-29", searchText({
    id: "jav:thread-1",
    name: "Innocent High",
    cast: ["Cadence Carter"],
  }));
  return openTitlesDb(db);
}

test("searchText folds codes so WAAA 629 matches WAAA-629", () => {
  const text = searchText({ id: "jav:WAAA-629", name: "WAAA-629 title", cast: ["田中"] });
  assert.match(text, /waaa-629/);
  assert.match(text, /waaa629/);
  assert.match(text, /田中/);
});

test("queryCatalog puts metadata titles first, bare titles last", () => {
  const db = memoryDb();
  const first = queryCatalog(db, { catalogId: "jav-censored", skip: 0, page: 2 });
  // Rank-0 rows (JUR-093 09-23, ABF-389 07-01) come before rank-1 rows.
  assert.deepEqual(first.map((row) => row.id), ["jav:JUR-093", "jav:ABF-389"]);
  const next = queryCatalog(db, { catalogId: "jav-censored", skip: 2, page: 2 });
  assert.deepEqual(next.map((row) => row.id), ["jav:WAAA-629"]);
  const found = queryCatalog(db, { catalogId: "jav-censored", search: "WAAA 629" });
  assert.deepEqual(found.map((row) => row.id), ["jav:WAAA-629"]);
  const cast = queryCatalog(db, { catalogId: "jav-censored", search: "宝生みさと" });
  assert.deepEqual(cast.map((row) => row.id), ["jav:JUR-093"]);
  const western = queryCatalog(db, { catalogId: "western", search: "cadence" });
  assert.deepEqual(western.map((row) => row.id), ["jav:thread-1"]);
});

test("queryTitle returns one mapped row", () => {
  const db = memoryDb();
  const item = queryTitle(db, "jav:JUR-093");
  assert.equal(item.id, "jav:JUR-093");
  assert.deepEqual(item.cast, ["宝生みさと"]);
  assert.equal(item.infoHash, "aaa");
  assert.equal(queryTitle(db, "jav:missing"), null);
});

function freeSetDb() {
  const db = new DatabaseSync(":memory:");
  createTitlesSchema(db);
  const insert = db.prepare(`INSERT INTO titles (id, category, name, description, genres_json, posted_at, search_text) VALUES (?, ?, ?, ?, ?, ?, ?)`);
  const dates = [
    "2026-11-03", "2026-11-02", "2026-11-01", "2026-10-31", "2026-10-30",
    "2026-10-29", "2026-10-28", "2026-10-27", "2026-10-26", "2026-10-25",
    "2026-10-24", "2026-10-23", "2026-10-22", "2026-10-21", "2026-10-20",
    "2026-10-19", "2026-10-18", "2026-10-17", "2026-10-16", "2026-10-15",
  ];
  for (let i = 0; i < 20; i++) {
    const n = String(i + 1).padStart(3, "0");
    // TST-001 (i=0, newest) is the only meta row (genres present) → rank 0,
    // so it sorts first even though it is also the newest. The remaining
    // no-meta rows (rank 1) fill positions 2..N newest-first.
    const genres = i === 0 ? '["Drama"]' : "[]";
    insert.run(`jav:TST-${n}`, "jav-censored", `TST-${n}`, "", genres, dates[i], `tst ${n}`);
  }
  return openTitlesDb(db);
}

test("isInFreeSet: meta rows first, then newest FREE_CATALOG_SIZE in the free set", () => {
  const db = freeSetDb();
  for (let i = 1; i <= FREE_CATALOG_SIZE; i++) {
    const n = String(i).padStart(3, "0");
    assert.equal(isInFreeSet(db, `jav:TST-${n}`), true, `TST-${n} should be in free set`);
  }
  for (let i = FREE_CATALOG_SIZE + 1; i <= 20; i++) {
    const n = String(i).padStart(3, "0");
    assert.equal(isInFreeSet(db, `jav:TST-${n}`), false, `TST-${n} should be outside free set`);
  }
  assert.equal(isInFreeSet(db, "jav:missing"), false);
});

function hntSeriesDb() {
  const db = new DatabaseSync(":memory:");
  db.exec(`
    CREATE TABLE titles (
      id TEXT PRIMARY KEY,
      category TEXT NOT NULL,
      name TEXT NOT NULL,
      description TEXT DEFAULT '',
      poster TEXT DEFAULT '',
      background TEXT DEFAULT '',
      runtime TEXT DEFAULT '',
      info_hash TEXT DEFAULT '',
      stream_url TEXT DEFAULT '',
      series_key TEXT DEFAULT '',
      episode_num INTEGER DEFAULT 0,
      posted_at TEXT DEFAULT '',
      release_info TEXT DEFAULT '',
      imdb_rating TEXT DEFAULT '',
      genres_json TEXT DEFAULT '',
      released TEXT DEFAULT ''
    );
  `);
  const insert = db.prepare(`
    INSERT INTO titles (id, category, name, description, poster, series_key, episode_num, posted_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  `);
  // Multi-episode series with noisy raw names:
  insert.run("hnt:nyaa-4742254", "hentai-uncensored",
    "[geckyzz] My Matchmaking Partner Is My Student - S01 (...) [BATCH]", "An anime series.", "https://example.com/p1.jpg",
    "my-matchmaking-partner-is-my-student", 1, "2026-11-08");
  insert.run("hnt:nyaa-4742255", "hentai-uncensored",
    "[geckyzz] My Matchmaking Partner Is My Student - S02 (...)", "", "",
    "my-matchmaking-partner-is-my-student", 2, "2026-11-08");
  insert.run("hnt:nyaa-4742256", "hentai-uncensored",
    "[geckyzz] My Matchmaking Partner Is My Student - S03 (...)", "", "",
    "my-matchmaking-partner-is-my-student", 3, "2026-11-08");
  // Solo series (single episode) keeps its raw name.
  insert.run("hnt:nyaa-4700001", "hentai-censored",
    "[HentaiHub] Some Solo Release 720p", "", "", "solo-4700001", 1, "2026-11-01");
  return db;
}

test("seriesDisplayName derives a clean title from the slug", () => {
  assert.equal(seriesDisplayName("my-matchmaking-partner-is-my-student"), "My Matchmaking Partner Is My Student");
  // Quality tokens that survive in slugs are dropped.
  assert.equal(seriesDisplayName("ichigo-aika-strawberry-elegy-audio-eng-sub"), "Ichigo Aika Strawberry Elegy");
  assert.equal(seriesDisplayName("solo-4700001"), "Solo 4700001");
  assert.equal(seriesDisplayName(""), "");
});

test("querySeriesList returns clean series names with episode counts", () => {
  const db = hntSeriesDb();
  const series = querySeriesList(db, { category: "hentai-uncensored", limit: 10 });
  assert.equal(series.length, 1);
  assert.equal(series[0].seriesKey, "my-matchmaking-partner-is-my-student");
  assert.equal(series[0].name, "My Matchmaking Partner Is My Student");
  assert.equal(series[0].epCount, 3);
  assert.match(series[0].poster, /p1\.jpg$/);
  const censored = querySeriesList(db, { category: "hentai-censored", limit: 10 });
  assert.equal(censored.length, 1);
  assert.equal(censored[0].name, "[HentaiHub] Some Solo Release 720p");
});

test("querySeriesMeta orders episodes s01..sN and sets defaultVideoId", () => {
  const db = hntSeriesDb();
  const meta = querySeriesMeta(db, "my-matchmaking-partner-is-my-student");
  assert.equal(meta.type, "series");
  assert.equal(meta.name, "My Matchmaking Partner Is My Student");
  assert.deepEqual(meta.episodes.map((e) => e.name), ["Episode 1", "Episode 2", "Episode 3"]);
  assert.equal(meta.behaviorHints.defaultVideoId, "hnt:my-matchmaking-partner-is-my-student:1");
});

test("querySeriesMeta: solo series keeps its raw release name", () => {
  const db = hntSeriesDb();
  const meta = querySeriesMeta(db, "solo-4700001");
  assert.equal(meta.name, "[HentaiHub] Some Solo Release 720p");
  assert.equal(meta.episodes.length, 1);
  assert.equal(querySeriesMeta(db, "no-such-series"), null);
});

test("querySeriesMeta: fanart (background) falls back to the series poster", () => {
  const db = hntSeriesDb();
  const meta = querySeriesMeta(db, "my-matchmaking-partner-is-my-student");
  // No row carries a background, so the series poster is used as fanart.
  assert.equal(meta.background, "https://example.com/p1.jpg");
  assert.equal(meta.poster, "https://example.com/p1.jpg");
});

test("querySeriesMeta excludes the series row (episode_num = 0) from episodes", () => {
  const db = new DatabaseSync(":memory:");
  db.exec(`
    CREATE TABLE titles (
      id TEXT PRIMARY KEY,
      category TEXT NOT NULL,
      name TEXT NOT NULL,
      description TEXT DEFAULT '',
      poster TEXT DEFAULT '',
      background TEXT DEFAULT '',
      runtime TEXT DEFAULT '',
      info_hash TEXT DEFAULT '',
      stream_url TEXT DEFAULT '',
      series_key TEXT DEFAULT '',
      episode_num INTEGER DEFAULT 0,
      posted_at TEXT DEFAULT '',
      release_info TEXT DEFAULT '',
      imdb_rating TEXT DEFAULT '',
      genres_json TEXT DEFAULT '',
      released TEXT DEFAULT ''
    );
  `);
  const insert = db.prepare(`
    INSERT INTO titles (id, category, name, description, poster, series_key, episode_num, posted_at, release_info, imdb_rating, genres_json)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);
  insert.run("hnt:sister-breeder", "hentai", "Sister Breeder", "A series.", "https://hentai.tv/uploads/ep1.jpg", "sister-breeder", 0, "2026-11-11",
    "Brand: T Rex • Quality: 1080p • Rating: 9.7 • Year: 2025 • Language: English • Released: 2025-10-03", "9.7", JSON.stringify(["Big Boobs", "Blow Job"]));
  insert.run("hnt:sister-breeder:1", "hentai", "Sister Breeder Episode 1", "", "https://hentai.tv/uploads/ep1h.jpg", "sister-breeder", 1, "2026-11-11",
    "Brand: T Rex • Quality: 1080p • Rating: 9.7 • Year: 2025 • Language: English • Released: 2025-10-03", "9.7", JSON.stringify(["Big Boobs", "Blow Job"]));
  insert.run("hnt:sister-breeder:2", "hentai", "Sister Breeder Episode 2", "", "https://hentai.tv/uploads/ep2h.jpg", "sister-breeder", 2, "2026-11-11",
    "Brand: T Rex • Quality: 1080p • Rating: 9.7 • Year: 2025 • Language: English • Released: 2025-10-03", "9.7", JSON.stringify(["Big Boobs", "Blow Job"]));
  const meta = querySeriesMeta(db, "sister-breeder");
  assert.deepEqual(meta.episodes.map((e) => e.name), ["Episode 1", "Episode 2"]);
  assert.equal(meta.behaviorHints.defaultVideoId, "hnt:sister-breeder:1");
  assert.equal(meta.poster, "https://hentai.tv/uploads/ep1.jpg");
  assert.equal(meta.releaseInfo, "Brand: T Rex • Quality: 1080p • Rating: 9.7 • Year: 2025 • Language: English • Released: 2025-10-03");
  assert.equal(meta.imdbRating, "9.7");
  assert.deepEqual(meta.genres, ["Big Boobs", "Blow Job"]);
  assert.deepEqual(meta.episodes[0].genres, ["Big Boobs", "Blow Job"]);
  assert.equal(meta.episodes[0].releaseInfo, "Brand: T Rex • Quality: 1080p • Rating: 9.7 • Year: 2025 • Language: English • Released: 2025-10-03");
});

function hntPagingDb() {
  const db = new DatabaseSync(":memory:");
  db.exec(`
    CREATE TABLE titles (
      id TEXT PRIMARY KEY,
      category TEXT NOT NULL,
      name TEXT NOT NULL,
      description TEXT DEFAULT '',
      poster TEXT DEFAULT '',
      background TEXT DEFAULT '',
      runtime TEXT DEFAULT '',
      info_hash TEXT DEFAULT '',
      stream_url TEXT DEFAULT '',
      series_key TEXT DEFAULT '',
      episode_num INTEGER DEFAULT 0,
      posted_at TEXT DEFAULT ''
    );
  `);
  const insert = db.prepare(`
    INSERT INTO titles (id, category, name, series_key, episode_num, posted_at)
    VALUES (?, ?, ?, ?, ?, ?)
  `);
  insert.run("hnt:nyaa-1", "hentai-censored", "Series A - E01", "series-a", 1, "2026-11-05");
  insert.run("hnt:nyaa-2", "hentai-censored", "Series B - E01", "series-b", 1, "2026-11-06");
  insert.run("hnt:nyaa-3", "hentai-censored", "Series C - E01", "series-c", 1, "2026-11-07");
  return db;
}

test("querySeriesList: SQL OFFSET pages past the first limit (no empty pages)", () => {
  const db = hntPagingDb();
  const page1 = querySeriesList(db, { category: "hentai-censored", limit: 2, offset: 0 });
  // posted_at DESC, series_key ASC: C (11-07) then B (11-06) fill page 1.
  assert.deepEqual(page1.map((r) => r.seriesKey), ["series-c", "series-b"]);
  const page2 = querySeriesList(db, { category: "hentai-censored", limit: 2, offset: 2 });
  assert.deepEqual(page2.map((r) => r.seriesKey), ["series-a"]);
  const beyond = querySeriesList(db, { category: "hentai-censored", limit: 2, offset: 4 });
  assert.equal(beyond.length, 0);
});

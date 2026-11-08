import { test } from "node:test";
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import { FREE_CATALOG_SIZE, isInFreeSet, openTitlesDb, queryCatalog, queryTitle, searchText } from "./titles-db.js";

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

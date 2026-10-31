import { test } from "node:test";
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import { openTitlesDb, queryCatalog, queryTitle, searchText } from "./titles-db.js";

function memoryDb() {
  const db = new DatabaseSync(":memory:");
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
  const insert = db.prepare(`
    INSERT INTO titles (id, category, name, cast_json, info_hash, posted_at, search_text)
    VALUES (?, ?, ?, ?, ?, ?, ?)
  `);
  insert.run("jav:JUR-093", "jav-censored", "JUR-093 無名の原石妻", '["宝生みさと"]', "aaa", "2026-09-23", searchText({
    id: "jav:JUR-093",
    name: "JUR-093 無名の原石妻",
    cast: ["宝生みさと"],
  }));
  insert.run("jav:WAAA-629", "jav-censored", "WAAA-629 something", '["田中"]', "bbb", "2026-08-01", searchText({
    id: "jav:WAAA-629",
    name: "WAAA-629 something",
    cast: ["田中"],
  }));
  insert.run("jav:ABF-389", "jav-censored", "ABF-389 prestige", "[]", "ccc", "2026-07-01", searchText({
    id: "jav:ABF-389",
    name: "ABF-389 prestige",
    cast: [],
  }));
  insert.run("jav:thread-1", "western", "Innocent High", '["Cadence Carter"]', "ddd", "2026-10-29", searchText({
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

test("queryCatalog pages newest first and filters search", () => {
  const db = memoryDb();
  const first = queryCatalog(db, { catalogId: "jav-censored", skip: 0, page: 2 });
  assert.deepEqual(first.map((row) => row.id), ["jav:JUR-093", "jav:WAAA-629"]);
  const next = queryCatalog(db, { catalogId: "jav-censored", skip: 2, page: 2 });
  assert.deepEqual(next.map((row) => row.id), ["jav:ABF-389"]);
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

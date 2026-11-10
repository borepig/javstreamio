// One-time (idempotent) migration: add the precomputed `meta_rank` column and
// the (category, meta_rank, posted_at, id) index to data/titles.sqlite so the
// catalog/meta queries are index-served instead of scanning 45k rows with a
// CASE expression (~3.4s/page on the t3.micro).
//
// Usage (from debrid-app):
//   node scripts/migrate-meta-rank.mjs
//
// Run on the live server before/after a deploy: the app opens titles.sqlite
// read-only, so the migration must be a separate writable step.

import { DEFAULT_SQLITE, migrateTitlesDb, resolveSqlitePath } from "../lib/titles-db.js";

const file = resolveSqlitePath() || DEFAULT_SQLITE;
migrateTitlesDb(file);
console.log(`titles.sqlite meta_rank migration applied to ${file}`);

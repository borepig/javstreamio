// One-shot: create debrid-app/data/keys.sqlite (idempotent).
import { DatabaseSync } from "node:sqlite";
import { existsSync } from "node:fs";
import path from "node:path";

const file = path.join(process.cwd(), "data", "keys.sqlite");
const isNew = !existsSync(file);
const db = new DatabaseSync(file, { readOnly: false });
db.exec(`
CREATE TABLE IF NOT EXISTS paid_keys (
  key TEXT PRIMARY KEY,
  plan TEXT NOT NULL CHECK (plan IN ('monthly','lifetime')),
  issued_at TEXT,
  expires_at TEXT,
  max_devices INTEGER NOT NULL DEFAULT 3
);
CREATE TABLE IF NOT EXISTS key_devices (
  key TEXT NOT NULL,
  device_id TEXT NOT NULL,
  first_seen TEXT,
  PRIMARY KEY (key, device_id)
);`);

if (isNew) {
  db.prepare(
    "INSERT INTO paid_keys (key, plan, issued_at, expires_at, max_devices) VALUES ('javpro-test', 'lifetime', '2026-11-04', NULL, 3)"
  ).run();
  console.log("created keys.sqlite with schema + test key javpro-test");
} else {
  console.log("keys.sqlite already existed; schema ensured, no seed");
}
db.close();

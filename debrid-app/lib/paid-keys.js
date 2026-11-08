// Paid-tier keys are stored in data/keys.sqlite (read-only on Vercel — the file
// ships inside the deployment). The owner or the Squeezy webhook inserts rows;
// a redeploy ships them. Keys issued by the webhook go to Vercel KV when KV_URL
// is set, so `isValidPaidKeyAsync` checks both stores.
//
// Plans:
//   - monthly ($5/mo): expires 30 days after issued_at
//   - annual ($10/yr): expires 365 days after issued_at
//   - lifetime ($10 once, 30-day launch special): key never expires;
//     the 30-day countdown is the limited-time offer window, not the key.
//
// Free tier (no valid key): catalog capped at FREE_CATALOG_SIZE per category,
// meta gated by isInFreeSet; all debrid services work on every tier.

import { DatabaseSync } from "node:sqlite";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CANDIDATES = [
  path.join(HERE, "../data/keys.sqlite"),
  path.join(process.cwd(), "data", "keys.sqlite"),
];

export function resolveKeysSqlitePath() {
  return CANDIDATES.find((file) => existsSync(file)) || "";
}

let keysDb;
export function getKeysDb() {
  if (keysDb !== undefined) return keysDb;
  const file = resolveKeysSqlitePath();
  try {
    keysDb = file ? new DatabaseSync(file, { readOnly: true }) : null;
  } catch {
    keysDb = null;
  }
  return keysDb;
}

// `dbArg` lets unit tests inject an in-memory DB; omit it to read keys.sqlite.
export function isValidPaidKey(key, dbArg) {
  const k = String(key || "").trim();
  if (!k) return false;
  const source = dbArg || getKeysDb();
  if (!source) return false;
  const row = source
    .prepare("SELECT plan, expires_at FROM paid_keys WHERE key = ?")
    .get(k);
  if (!row) return false;
  // Every plan carries an expires_at (monthly 30d, annual 365d, lifetime 30d
  // launch special). Any key past its expiry is invalid.
  if (row.expires_at && Date.now() > Date.parse(row.expires_at)) {
    return false;
  }
  return true;
}

function kvHeaders() {
  const headers = { "Content-Type": "application/json" };
  if (process.env.KV_TOKEN) headers.Authorization = `Bearer ${process.env.KV_TOKEN}`;
  return headers;
}

export async function isValidPaidKeyAsync(key) {
  const k = String(key || "").trim();
  if (!k) return false;
  if (isValidPaidKey(k)) return true;
  const kvUrl = process.env.KV_URL;
  if (!kvUrl) return false;
  try {
    const res = await fetch(`${kvUrl}/key/javpro-key-${encodeURIComponent(k)}`, {
      headers: kvHeaders(),
      cache: "no-store",
    });
    if (!res.ok) return false;
    const { value } = await res.json();
    const row = JSON.parse(value);
    if (row.expiresAt && Date.now() > Date.parse(row.expiresAt)) {
      return false;
    }
    return true;
  } catch {
    return false;
  }
}

// Device control: each key row carries max_devices (default 3). The client
// (debrid-app page) sends a stable ?device= id; this tracker counts distinct
// devices per key. Vercel's runtime FS is read-only, so tracking is in-memory
// per function instance — a best-effort soft limit.
const DEVICE_LIMIT_DEFAULT = 3;
const deviceSets = new Map();

export function getDeviceLimit(key, dbArg) {
  const source = dbArg || getKeysDb();
  const row = source && source.prepare("SELECT max_devices FROM paid_keys WHERE key = ?").get(key);
  return (row && row.max_devices) || DEVICE_LIMIT_DEFAULT;
}

export function registerDevice(key, deviceId, dbArg) {
  const limit = getDeviceLimit(key, dbArg);
  const id = String(deviceId || "").trim();
  const set = deviceSets.get(key) || new Set();
  const isNew = Boolean(id) && !set.has(id);
  if (isNew) set.add(id);
  if (id) deviceSets.set(key, set);
  const count = set.size;
  return { allowed: count <= limit, count, limit, isNew };
}

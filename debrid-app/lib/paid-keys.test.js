import { test } from "node:test";
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import { isValidPaidKey, getDeviceLimit, registerDevice } from "./paid-keys.js";

// Build an in-memory keys DB with paid_keys rows, so tests don't touch the
// shipped data/keys.sqlite.
function makeDb(rows) {
  const db = new DatabaseSync(":memory:");
  db.exec(
    "CREATE TABLE paid_keys (key TEXT PRIMARY KEY, plan TEXT NOT NULL, issued_at TEXT, expires_at TEXT, max_devices INTEGER NOT NULL DEFAULT 3)"
  );
  const ins = db.prepare(
    "INSERT INTO paid_keys (key, plan, issued_at, expires_at, max_devices) VALUES (?, ?, ?, ?, ?)"
  );
  for (const r of rows) ins.run(r.key, r.plan, r.issuedAt, r.expiresAt, r.maxDevices ?? 3);
  return db;
}

test("unknown or empty key is not paid", () => {
  const db = makeDb([]);
  assert.equal(isValidPaidKey("", db), false);
  assert.equal(isValidPaidKey("nope", db), false);
  assert.equal(isValidPaidKey(undefined, db), false);
});

test("lifetime key never expires (launch special offer is 30 days, not the key)", () => {
  const db = makeDb([{ key: "test-lifetime", plan: "lifetime", issuedAt: "2026-11-04", expiresAt: null }]);
  assert.equal(isValidPaidKey("test-lifetime", db), true);
});

test("unexpired annual key is valid", () => {
  const db = makeDb([
    { key: "test-annual", plan: "annual", issuedAt: "2026-11-04", expiresAt: "2027-11-04" },
  ]);
  assert.equal(isValidPaidKey("test-annual", db), true);
});

test("expired annual key is not valid", () => {
  const db = makeDb(
    [{ key: "test-annual-expired", plan: "annual", issuedAt: "2025-01-01", expiresAt: "2026-01-01" }]
  );
  assert.equal(isValidPaidKey("test-annual-expired", db), false);
});

test("unexpired monthly key is valid", () => {
  const db = makeDb([
    { key: "test-monthly", plan: "monthly", issuedAt: "2026-11-04", expiresAt: "2026-12-04" },
  ]);
  assert.equal(isValidPaidKey("test-monthly", db), true);
});

test("expired monthly key is not valid", () => {
  const db = makeDb([
    { key: "test-expired", plan: "monthly", issuedAt: "2020-01-01", expiresAt: "2020-02-01" },
  ]);
  assert.equal(isValidPaidKey("test-expired", db), false);
});

test("shipped keys.sqlite: seeded javpro-test key is valid", () => {
  // No dbArg: resolves debrid-app/data/keys.sqlite from disk (the test runs
  // with cwd = debrid-app, so the file is found).
  assert.equal(isValidPaidKey("javpro-test"), true);
  assert.equal(isValidPaidKey("javpro-0001"), false);
});

test("registerDevice enforces the per-key device limit", () => {
  const db = makeDb([{ key: "k1", plan: "lifetime", issuedAt: "2026-11-04", expiresAt: null, maxDevices: 2 }]);
  assert.equal(registerDevice("k1", "d1", db).allowed, true);
  assert.equal(registerDevice("k1", "d2", db).allowed, true);
  assert.equal(registerDevice("k1", "d3", db).allowed, false);
  // Re-registering a known device does not change the count.
  const again = registerDevice("k1", "d3", db);
  assert.equal(again.allowed, false);
  assert.equal(again.isNew, false);
});

test("getDeviceLimit reads max_devices, falls back to 3", () => {
  const db = makeDb([{ key: "k2", plan: "lifetime", issuedAt: "2026-11-04", expiresAt: null, maxDevices: 5 }]);
  assert.equal(getDeviceLimit("k2", db), 5);
  assert.equal(getDeviceLimit("missing", db), 3);
});

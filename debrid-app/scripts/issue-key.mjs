// Key-pool issuance for the manual BMAC flow.
//
// Pool: a set of pre-issued keys lives in data/keys.sqlite (paid_keys).
// When a buyer pays via Buy Me a Coffee, joe emails them one unissued key
// from the pool and records the issuance, so the same key is never handed
// out twice.
//
// Usage:
//   node scripts/issue-key.mjs <monthly|annual|lifetime> [buyer-email] -> prints the key to email
//   node scripts/issue-key.mjs renew <key>                            -> monthly renewal (expires_at = now + 30d)
//   node scripts/issue-key.mjs add <monthly|annual|lifetime> <count>  -> top up the pool
//   node scripts/issue-key.mjs pool                                    -> pool status
//
// After inserting/updating rows, commit data/keys.sqlite and redeploy
// (`npx vercel --prod --yes`) so the live app validates the new keys.

import { DatabaseSync } from "node:sqlite";
import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DB = path.join(HERE, "../data/keys.sqlite");

const today = () => new Date().toISOString().slice(0, 10);
const plusDays = (days) => new Date(Date.now() + days * 86400 * 1000).toISOString().slice(0, 10);
const plus30d = () => plusDays(30);
// Expiry per plan: monthly 30d, annual 365d, lifetime 30d (launch special).
const PLAN_DAYS = { monthly: 30, annual: 365, lifetime: 30 };
const randKey = () =>
  "javpro-" + createHash("sha256").update(String(Math.random())).digest("hex").slice(0, 8);

const db = new DatabaseSync(DB, { readOnly: false });
db.exec("CREATE TABLE IF NOT EXISTS key_issuances (key TEXT PRIMARY KEY, buyer TEXT, issued_at TEXT)");

const [, , cmd, a, b] = process.argv;

if (cmd === "add") {
  const plan = a;
  const count = parseInt(b, 10);
  if (!PLAN_DAYS[plan] || !count || count < 1) {
    console.error("Usage: node scripts/issue-key.mjs add <monthly|annual|lifetime> <count>");
    process.exit(1);
  }
  const insert = db.prepare(
    "INSERT INTO paid_keys (key, plan, issued_at, expires_at, max_devices) VALUES (?, ?, ?, ?, 3)"
  );
  const expires = plusDays(PLAN_DAYS[plan]);
  const tx = (keys) => {
    for (const key of keys) insert.run(key, plan, today(), expires);
  };
  db.exec("BEGIN");
  try {
    const keys = Array.from({ length: count }, randKey);
    tx(keys);
    db.exec("COMMIT");
    console.log(`Added ${count} ${plan} keys to the pool:`);
    for (const k of keys) console.log("  " + k);
  } catch (e) {
    db.exec("ROLLBACK");
    throw e;
  }
} else if (cmd === "renew") {
  const key = a;
  if (!key) {
    console.error("Usage: node scripts/issue-key.mjs renew <key>");
    process.exit(1);
  }
  const res = db.prepare("UPDATE paid_keys SET expires_at = ? WHERE key = ?").run(plus30d(), key);
  if (res.changes === 0) {
    console.error(`Key not found: ${key}`);
    process.exit(1);
  }
  console.log(`${key} renewed -> expires ${plus30d()}`);
} else if (cmd === "pool") {
  for (const plan of ["monthly", "annual", "lifetime"]) {
    const total = db.prepare("SELECT COUNT(*) c FROM paid_keys WHERE plan = ?").get(plan).c;
    const issued = db.prepare(
      "SELECT COUNT(*) c FROM paid_keys k WHERE k.plan = ? AND EXISTS (SELECT 1 FROM key_issuances i WHERE i.key = k.key)"
    ).get(plan).c;
    console.log(`${plan}: ${total} in pool, ${issued} issued, ${total - issued} available`);
  }
} else {
  // Default: issue the next available pool key for the plan.
  const plan = cmd;
  const buyer = a || "";
  if (!PLAN_DAYS[plan]) {
    console.error("Usage: node scripts/issue-key.mjs <monthly|annual|lifetime> [buyer-email]");
    process.exit(1);
  }
  const row = db.prepare(
    "SELECT key, plan, expires_at FROM paid_keys WHERE plan = ? AND key NOT IN (SELECT key FROM key_issuances) ORDER BY key ASC LIMIT 1"
  ).get(plan);
  if (!row) {
    console.error(`No available ${plan} keys left. Top up with: node scripts/issue-key.mjs add ${plan} <count>`);
    process.exit(1);
  }
  db.prepare("INSERT INTO key_issuances (key, buyer, issued_at) VALUES (?, ?, ?)").run(
    row.key,
    buyer,
    today(),
  );
  const suffix = row.expires_at ? `, expires ${row.expires_at}` : ", never expires";
  console.log(row.key);
  if (buyer) console.log(`Email ${row.key} to ${buyer} (plan: ${row.plan}${suffix})`);
}

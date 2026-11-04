// Subscriber keys for the paid tier of the JAVSTREAMIO Stremio addon.
//
// Issue one key per buyer:
//   - monthly subscription ($5.99/mo, via BMAC Monthly Support / membership):
//     { key, plan: "monthly", issuedAt, expiresAt }
//   - lifetime ($29 one-time):
//     { key, plan: "lifetime" }  // never expires
//
// Free tier (no valid key) is capped at FREE_CATALOG_SIZE per catalog and can
// only open meta for the newest FREE_CATALOG_SIZE titles of each category.

const PAID_KEYS = [
  // { key: "javpro-0001", plan: "lifetime" },
  // { key: "javpro-0002", plan: "monthly", issuedAt: "2026-11-04", expiresAt: "2026-12-04" },
];

export function isValidPaidKey(key) {
  const k = String(key || "").trim();
  if (!k) return false;
  const entry = PAID_KEYS.find((e) => e.key === k);
  if (!entry) return false;
  if (entry.plan === "monthly" && entry.expiresAt && Date.now() > Date.parse(entry.expiresAt)) {
    return false;
  }
  return true;
}

export { PAID_KEYS };

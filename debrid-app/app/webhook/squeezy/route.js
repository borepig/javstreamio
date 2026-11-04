import { NextResponse } from "next/server";
import { createHmac, timingSafeEqual } from "node:crypto";

// Lemon Squeezy webhook: when an order is paid, issue a paid key.
//
// Real Squeezy order_created payload shape (docs: "Webhook requests"):
//   { "meta": {...}, "data": { "type": "orders", "id": "1",
//     "attributes": { "identifier": "<uuid>", "status": "paid",
//       "first_order_item": { "product_name": "..." } } }
// Auth: X-Signature header = HMAC-SHA256 hex of the raw body, keyed with
// the signing secret set in the Squeezy webhook settings.
//
// The Vercel runtime filesystem is read-only, so keys issued here go to
// Vercel KV (env: KV_URL, KV_TOKEN) when configured — `isValidPaidKeyAsync`
// checks both KV and keys.sqlite. If KV is not configured, the response body
// contains the key row JSON so the owner can add it to data/keys.sqlite and
// redeploy.
//
// Env vars to set in Vercel project settings:
//   SQUEEZY_WEBHOOK_SECRET  — must match the signing secret in Squeezy
//   KV_URL / KV_TOKEN       — optional, enables durable auto-issuance

export const dynamic = "force-dynamic";

function kvHeaders() {
  const headers = { "Content-Type": "application/json" };
  if (process.env.KV_TOKEN) headers.Authorization = `Bearer ${process.env.KV_TOKEN}`;
  return headers;
}

async function kvSet(keyName, value) {
  const kvUrl = process.env.KV_URL;
  if (!kvUrl) return false;
  const res = await fetch(`${kvUrl}/key/${encodeURIComponent(keyName)}`, {
    method: "POST",
    headers: kvHeaders(),
    body: JSON.stringify({ value }),
    cache: "no-store",
  });
  return res.ok;
}

function planFromProductName(name) {
  return String(name || "").toLowerCase().includes("month") ? "monthly" : "lifetime";
}

function addDays(date, days) {
  const d = new Date(date.getTime() + days * 86400000);
  return d.toISOString().slice(0, 10);
}

export async function POST(req) {
  // HMAC verification needs the raw body, so read the body once as text.
  const rawBody = await req.text();

  const secret = process.env.SQUEEZY_WEBHOOK_SECRET;
  if (secret) {
    const digest = createHmac("sha256", secret).update(rawBody).digest("hex");
    const sig = (req.headers.get("x-signature") || "").toLowerCase();
    let sigOk = false;
    if (sig.length === digest.length) {
      sigOk = timingSafeEqual(Buffer.from(digest, "utf8"), Buffer.from(sig, "utf8"));
    }
    if (!sigOk) {
      return NextResponse.json({ error: "bad signature" }, { status: 403 });
    }
  }

  let payload;
  try {
    payload = JSON.parse(rawBody);
  } catch {
    return NextResponse.json({ error: "bad payload" }, { status: 400 });
  }

  const data = payload && payload.data;
  // Real Squeezy uses data.attributes; keep the legacy simulated shape
  // (data.order with externalId) working for curl-based tests.
  const attributes =
    (data && data.attributes) || (data && data.order) || payload || {};
  if (attributes.status !== "paid") {
    return NextResponse.json({ ignored: true, reason: `event ${payload && payload.meta && payload.meta.event_name} is not a paid order` });
  }

  const productName =
    (attributes.first_order_item && attributes.first_order_item.product_name) ||
    (attributes.product && attributes.product.name) ||
    "";
  const plan = planFromProductName(productName);
  const seedSource =
    attributes.identifier ||
    (data && data.order && data.order.externalId) ||
    (data && data.id) ||
    "00000000";
  const seed = String(seedSource).replace(/[^A-Za-z0-9-]/g, "").slice(-8);
  const key = `javpro-${seed || "0000"}`;
  const row = {
    key,
    plan,
    issuedAt: new Date().toISOString().slice(0, 10),
    expiresAt: plan === "monthly" ? addDays(new Date(), 30) : null,
    maxDevices: 3,
  };
  const persisted = await kvSet(`javpro-key-${key}`, JSON.stringify(row));
  return NextResponse.json({ key, plan: row.plan, issuedAt: row.issuedAt, expiresAt: row.expiresAt, persisted });
}

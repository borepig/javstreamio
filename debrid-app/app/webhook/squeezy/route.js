import { NextResponse } from "next/server";

// Lemon Squeezy webhook: when an order is paid, issue a paid key.
//
// The Vercel runtime filesystem is read-only, so keys issued here go to
// Vercel KV (env: KV_URL, KV_TOKEN) when configured — `isValidPaidKeyAsync`
// checks both KV and keys.sqlite. If KV is not configured, the response body
// contains the key row JSON so the owner can add it to data/keys.sqlite and
// redeploy.
//
// Env vars to set in Vercel project settings:
//   SQUEEZY_WEBHOOK_SECRET  — must match the secret in the Squeezy webhook config
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

function planFromProduct(product) {
  const name = String((product && product.name) || "").toLowerCase();
  return name.includes("month") ? "monthly" : "lifetime";
}

function addDays(date, days) {
  const d = new Date(date.getTime() + days * 86400000);
  return d.toISOString().slice(0, 10);
}

export async function POST(req) {
  const secret = process.env.SQUEEZY_WEBHOOK_SECRET || "";
  const got = req.headers.get("x-squeezy-webhook-secret") || "";
  if (secret && got !== secret) {
    return NextResponse.json({ error: "bad secret" }, { status: 403 });
  }

  let payload;
  try {
    payload = await req.json();
  } catch {
    return NextResponse.json({ error: "bad payload" }, { status: 400 });
  }

  const data = payload && payload.data;
  const order = (data && data.order) || (payload && payload.order) || payload || {};
  if (order.status !== "paid") {
    return NextResponse.json({ ignored: true, reason: `event ${payload && payload.event} is not a paid order` });
  }

  const plan = planFromProduct(order.product);
  const seed = String(order.externalId || order.id || "00000000")
    .replace(/[^A-Za-z0-9-]/g, "")
    .slice(-8);
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

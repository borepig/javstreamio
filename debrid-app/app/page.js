"use client";
import { useEffect, useState } from "react";
import { ADDON_NAME, ADDON_VERSION } from "../lib/version.js";

const SERVICES = [
  { id: "torbox", label: "Torbox" },
  { id: "real-debrid", label: "Real-Debrid" },
  { id: "alldebrid", label: "AllDebrid" },
  { id: "premiumize", label: "Premiumize" },
  { id: "debrid-link", label: "Debrid-Link" },
];

const LS_SERVICE = "jsio_debrid_service";
const LS_KEY = "jsio_debrid_key";
const LS_PAID_KEY = "jsio_paid_key";
const LS_DEVICE = "jsio_device_id";

const ADDON_BASE = "https://javstremio.vercel.app";
// Lemon Squeezy storefront — update the product slugs after creating the
// "JAV Pro Monthly $5.99" and "JAV Pro Lifetime $29" products.
const SQUEEZY_MONTHLY_URL = "https://www.lemonsqueezy.com/product/javpro-monthly";
const SQUEEZY_LIFETIME_URL = "https://www.lemonsqueezy.com/product/javpro-lifetime";

export default function Page() {
  const [service, setService] = useState("torbox");
  const [key, setKey] = useState("");
  const [paidKey, setPaidKey] = useState("");
  const [deviceId, setDeviceId] = useState("");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const s = localStorage.getItem(LS_SERVICE);
    const k = localStorage.getItem(LS_KEY);
    const pk = localStorage.getItem(LS_PAID_KEY);
    if (s && SERVICES.some((item) => item.id === s)) setService(s);
    if (k) setKey(k);
    if (pk) setPaidKey(pk);
    let id = localStorage.getItem(LS_DEVICE);
    if (!id) {
      id = "dev-" + Math.random().toString(36).slice(2, 12);
      localStorage.setItem(LS_DEVICE, id);
    }
    setDeviceId(id);
    setReady(true);
  }, []);

  useEffect(() => {
    if (!ready) return;
    localStorage.setItem(LS_SERVICE, service);
    localStorage.setItem(LS_KEY, key);
    localStorage.setItem(LS_PAID_KEY, paidKey);
  }, [ready, service, key, paidKey]);

  // Stremio requires the custom addon URL to end with /manifest.json (stremio-core
  // http_transport.rs: resource() rejects URLs that don't end with /manifest.json).
  // Free tier: bare manifest.json — newest 15 per category, Torbox/Real-Debrid
  // debrid + P2P. Paid tier: ?service=&token=&key=&device= unlocks the full
  // 41k catalog/meta, all debrid services and one-click playback.
  const params = [];
  if (service) params.push(`service=${service}`);
  if (key) params.push(`token=${encodeURIComponent(key)}`);
  if (paidKey) params.push(`key=${encodeURIComponent(paidKey)}`);
  if (deviceId) params.push(`device=${deviceId}`);
  const query = params.length ? `?${params.join("&")}` : "";
  const addonUrl = `${ADDON_BASE}/manifest.json${query}`;
  const stremioUrl = `stremio://${ADDON_BASE.replace(/^https:\/\//, "")}/manifest.json${query}`;
  const webInstall = `https://web.stremio.com/#/addons?addon=${encodeURIComponent(addonUrl)}`;

  async function copyAddon() {
    if (!addonUrl) return;
    await navigator.clipboard.writeText(addonUrl);
  }

  return (
    <main className="wrap">
      <h1>
        {ADDON_NAME} <span className="version">v{ADDON_VERSION}</span>
      </h1>
      <p className="sub">
        Pick a debrid service and paste your own API key. Keys stay in this browser only
        — this site never ships a Torbox or debrid key. Copy the addon link into Stremio.
      </p>
      <p className="disclaimer">
        This is only a metadata scraper, we do not host, encourage or allow using this to
        view content that you do not own.
      </p>

      <div className="card">
        <label>
          Debrid service
          <select value={service} onChange={(e) => setService(e.target.value)}>
            {SERVICES.map((s) => (
              <option key={s.id} value={s.id}>{s.label}</option>
            ))}
          </select>
          <span className="hint">
            Free tier: Torbox + Real-Debrid only. AllDebrid, Premiumize and Debrid-Link
            require a paid key.
          </span>
        </label>

        <label>
          API key <span className="hint">stored in browser localStorage only</span>
          <input
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="Paste your debrid API key"
          />
        </label>

        <label>
          Paid key <span className="hint">optional — unlocks the full 41k library</span>
          <input
            type="password"
            value={paidKey}
            onChange={(e) => setPaidKey(e.target.value)}
            placeholder="Paste your subscription / lifetime key"
          />
        </label>
      </div>

      <div className="card">
        <h2>Stremio addon link</h2>
        <p>
          Uninstall any older copy first. Add this URL (Settings → Add-ons → Custom
          URL). Without a paid key this is the free tier — the newest 15 titles per
          category, Torbox/Real-Debrid + P2P playback.
          {key &&
            ` It includes your ${SERVICES.find((item) => item.id === service)?.label || service} key, which powers one-click debrid playback. Without that key, Play is a black screen.`}
          {paidKey &&
            " Your paid key unlocks the full 41k catalog, enriched metadata, and all debrid services."}
        </p>
        <code className="url">{addonUrl}</code>
        <div className="row">
          <a className="install" href={stremioUrl}>Open in Stremio</a>
          <a className="secondary" href={webInstall}>Install on Stremio Web</a>
          <button className="secondary" onClick={copyAddon}>Copy link</button>
        </div>
      </div>

      <div className="card tiers">
        <h2>Plans</h2>
        <div className="tier-grid">
          <div className="tier">
            <h3>Free — $0</h3>
            <ul>
              <li>Newest 15 titles per category</li>
              <li>Debrid playback: Torbox + Real-Debrid</li>
              <li>P2P / magnet fallback</li>
            </ul>
            <span className="free-note">No key needed</span>
          </div>
          <div className="tier">
            <h3>Monthly — $5.99/mo</h3>
            <ul>
              <li>Full 41k catalog + enriched metadata</li>
              <li>All 5 debrid services</li>
              <li>Key renews every 30 days</li>
            </ul>
            <a className="buy" href={SQUEEZY_MONTHLY_URL} target="_blank" rel="noreferrer">
              Buy monthly
            </a>
          </div>
          <div className="tier">
            <h3>Lifetime — $29</h3>
            <ul>
              <li>Everything, forever</li>
            </ul>
            <a className="buy" href={SQUEEZY_LIFETIME_URL} target="_blank" rel="noreferrer">
              Buy lifetime
            </a>
          </div>
        </div>
        <p className="getkey">
          How to get a key: buy a plan on the Squeezy store — when your order is paid, the
          webhook auto-issues a key. Paste it into the "Paid key" field above to unlock the
          full catalog and all debrid services.
        </p>
      </div>
    </main>
  );
}

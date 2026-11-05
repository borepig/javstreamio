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
// Buy Me a Coffee payment links (borepig). After payment, a pool key is
// issued via scripts/issue-key.mjs and emailed to the buyer.
const BMAC_MONTHLY_URL = "https://buymeacoffee.com/borepig?preset=5";
const BMAC_LIFETIME_URL = "https://buymeacoffee.com/borepig?preset=25";

export default function Page() {
  const [service, setService] = useState("torbox");
  const [key, setKey] = useState("");
  const [paidKey, setPaidKey] = useState("");
  const [deviceId, setDeviceId] = useState("");
  const [tier, setTier] = useState("lifetime");
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
  // Free tier: bare manifest.json — newest 15 per category, all debrid services
  // + P2P. Paid tier: ?service=&token=&key=&device= unlocks the full 41k
  // catalog/meta and one-click playback.
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
      <header className="head">
        <p className="eyebrow">Stremio Addon</p>
        <h1>
          {ADDON_NAME} <span className="version">{ADDON_VERSION}</span>
        </h1>
        <p className="sub">
          Pick a debrid service, paste your keys, copy the addon link into Stremio. Keys
          stay in this browser only.
        </p>
      </header>

      <section className="card tiers">
        <p className="micro">Plans</p>
        <h2>Choose your access</h2>
        <div className="tier-select" role="radiogroup" aria-label="Plan">
          {[
            ["free", "Free"],
            ["monthly", "Monthly"],
            ["lifetime", "Lifetime"],
          ].map(([id, label]) => (
            <button
              key={id}
              type="button"
              className={tier === id ? "tier-btn active" : "tier-btn"}
              aria-pressed={tier === id}
              onClick={() => setTier(id)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="tier-grid">
          <div className={tier === "free" ? "tier active" : "tier dimmed"}>
            <h3>Free</h3>
            <p className="price">$0</p>
            <ul>
              <li>Newest 15 titles per category (5 categories)</li>
              <li>Meta refreshed once a fortnight</li>
              <li>All debrid services + P2P / magnet fallback</li>
            </ul>
            {tier === "free" ? (
              <span className="free-note">
                No payment, no key — copy the addon link above and paste it into Stremio.
              </span>
            ) : (
              <span className="free-note">Included with the addon</span>
            )}
          </div>
          <div className={tier === "monthly" ? "tier active" : "tier dimmed"}>
            <h3>Monthly</h3>
            <p className="price">$5<span className="per">/ month</span></p>
            <ul>
              <li>Full catalog + enriched metadata</li>
              <li>Meta refreshed daily</li>
              <li>30-day key — renew to keep access</li>
            </ul>
            <a className="buy" href={BMAC_MONTHLY_URL} target="_blank" rel="noreferrer">
              Buy Me a Coffee
            </a>
          </div>
          <div className={tier === "lifetime" ? "tier best active" : "tier best dimmed"}>
            <span className="badge">Best value</span>
            <h3>Lifetime</h3>
            <p className="price">
              <s>$99</s> $25 <span className="once">once</span>
            </p>
            <ul>
              <li>Everything, forever</li>
              <li>Meta refreshed daily</li>
              <li>One payment — no renewal</li>
            </ul>
            <a className="buy" href={BMAC_LIFETIME_URL} target="_blank" rel="noreferrer">
              Buy Me a Coffee for a Week
            </a>
          </div>
        </div>
        <p className="getkey">
          After you pay, a key from our pool is issued to you by email. Paste it into the
          "Paid key" field below to unlock the full catalog.
        </p>
      </section>

      <section className="card">
        <p className="micro">Setup</p>
        <h2>Debrid service &amp; keys</h2>
        <label>
          Debrid service
          <select value={service} onChange={(e) => setService(e.target.value)}>
            {SERVICES.map((s) => (
              <option key={s.id} value={s.id}>{s.label}</option>
            ))}
          </select>
          <span className="hint">All 5 debrid services work on every tier.</span>
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
      </section>

      <section className="card">
        <p className="micro">Install</p>
        <h2>Stremio addon link</h2>
        <p>
          Uninstall any older copy first. Add this URL (Settings → Add-ons → Custom URL).
          Without a paid key this is the free tier: newest 15 titles per category, all
          debrid services + P2P playback.
          {key &&
            ` It includes your ${SERVICES.find((item) => item.id === service)?.label || service} key, which powers one-click debrid playback. Without that key, Play is a black screen.`}
          {paidKey &&
            " Your paid key unlocks the full 41k catalog and enriched metadata."}
        </p>
        <code className="url">{addonUrl}</code>
        <div className="row">
          <a className="install" href={stremioUrl}>Open in Stremio</a>
          <a className="secondary" href={webInstall}>Install on Stremio Web</a>
          <button className="secondary" onClick={copyAddon}>Copy link</button>
        </div>
      </section>

      <p className="disclaimer">
        This is only a metadata scraper. We do not host, encourage, or allow viewing content
        you do not own.
      </p>
    </main>
  );
}

"use client";
import { useEffect, useState } from "react";
import { ADDON_NAME, ADDON_VERSION } from "../lib/version.js";

const SERVICES = [
  { id: "torbox", label: "Torbox" },
  { id: "real-debrid", label: "Real-Debrid" },
  { id: "alldebrid", label: "AllDebrid" },
  { id: "premiumize", label: "Premiumize" },
  { id: "debrid-link", label: "Debrid-Link" },
  { id: "pikpak", label: "PikPak" },
];

const LS_SERVICE = "jsio_debrid_service";
const LS_KEY = "jsio_debrid_key";
const LS_PAID_KEY = "jsio_paid_key";
const LS_DEVICE = "jsio_device_id";

// Live home moved off Vercel (402 team fair-use block) to the user's EC2 +
// DynDNS domain. Keep this in sync with debrid-app/aws/cloudformation.yaml.
const ADDON_BASE = "https://javstremio.ddns.net";
// Buy Me a Coffee payment links (borepig). After payment, a pool key is
// issued via scripts/issue-key.mjs and emailed to the buyer.
const BMAC_LIFETIME_URL = "https://buymeacoffee.com/borepig?preset=10";
// Annual and lifetime both use the $10 preset (annual = $10/yr, lifetime
// = $10 once / 30-day launch special). If a separate BMAC preset is created
// for annual, point this at its preset id.
const BMAC_ANNUAL_URL = "https://buymeacoffee.com/borepig?preset=10";

export default function Page() {
  const [service, setService] = useState("torbox");
  const [key, setKey] = useState("");
  const [paidKey, setPaidKey] = useState("");
  const [deviceId, setDeviceId] = useState("");
  const [tier, setTier] = useState("annual");
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
        <div className="tier-grid">
          <div
            className={tier === "free" ? "tier active" : "tier dimmed"}
            role="button"
            tabIndex={0}
            onClick={() => setTier("free")}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") setTier("free");
            }}
          >
            <h3>Free</h3>
            <p className="price">$0</p>
            <ul>
              <li>4 categories: JAV Censored, JAV Uncensored, Western, Others — newest 15 titles each</li>
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
          <div
            className={tier === "annual" ? "tier best active" : "tier best dimmed"}
            role="button"
            tabIndex={0}
            onClick={() => setTier("annual")}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") setTier("annual");
            }}
          >
            <span className="badge">Best value</span>
            <h3>Annual</h3>
            <p className="price">$10 <span className="once">per year</span></p>
            <ul>
              <li>12 months of access</li>
              <li>All 9 categories: JAV Censored, JAV Uncensored, Western, Others, Hong Kong AV, Hentai Censored, Hentai Uncensored, Korean AV, Chinese AV</li>
              <li>Meta refreshed daily</li>
              <li>One yearly payment</li>
            </ul>
            <a className="buy" href={BMAC_ANNUAL_URL} target="_blank" rel="noreferrer">
              Buy Me a Coffee
            </a>
          </div>
          <div
            className={tier === "lifetime" ? "tier best active" : "tier best dimmed"}
            role="button"
            tabIndex={0}
            onClick={() => setTier("lifetime")}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") setTier("lifetime");
            }}
          >
            <span className="badge">Launch special</span>
            <h3>Lifetime</h3>
            <p className="price">
              <s>$99</s> $10 <span className="once">once</span>
            </p>
            <ul>
              <li>Key never expires</li>
              <li>30-day launch special — offer ends 2026-12-04</li>
              <li>All 9 categories: JAV Censored, JAV Uncensored, Western, Others, Hong Kong AV, Hentai Censored, Hentai Uncensored, Korean AV, Chinese AV</li>
              <li>Meta refreshed daily</li>
            </ul>
            <a className="buy" href={BMAC_LIFETIME_URL} target="_blank" rel="noreferrer">
              Buy Me a Coffee
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
          <span className="hint">All 7 debrid services work on every tier.</span>
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
            placeholder="Paste your monthly / annual / lifetime key"
          />
        </label>
      </section>

      <section className="card">
        <p className="micro">Install</p>
        <h2>Stremio addon link</h2>
        <p>
          Uninstall any older copy first. Add this URL (Settings → Add-ons → Custom URL).
          Without a paid key this is the free tier: 4 categories (JAV Censored, JAV
          Uncensored, Western, Others) — newest 15 titles each, all debrid services + P2P
          playback. A paid key unlocks the other 5 categories: Hong Kong AV, Hentai Censored,
          Hentai Uncensored, Korean AV, Chinese AV.
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

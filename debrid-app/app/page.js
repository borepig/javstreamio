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

const ADDON_BASE = "https://javstremio.vercel.app";

export default function Page() {
  const [service, setService] = useState("torbox");
  const [key, setKey] = useState("");
  const [paidKey, setPaidKey] = useState("");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const s = localStorage.getItem(LS_SERVICE);
    const k = localStorage.getItem(LS_KEY);
    const pk = localStorage.getItem(LS_PAID_KEY);
    if (s && SERVICES.some((item) => item.id === s)) setService(s);
    if (k) setKey(k);
    if (pk) setPaidKey(pk);
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
  // Free tier: bare manifest.json (newest 15 per category, no debrid rows).
  // Paid tier: ?service=&token=<debrid key>&key=<paid key> unlocks the full 41k
  // catalog/meta plus one-click debrid playback.
  const params = [];
  if (service) params.push(`service=${service}`);
  if (key) params.push(`token=${encodeURIComponent(key)}`);
  if (paidKey) params.push(`key=${encodeURIComponent(paidKey)}`);
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
          category, magnet/P2P playback only.
          {key &&
            ` It includes your ${SERVICES.find((item) => item.id === service)?.label || service} key, which powers one-click debrid playback. Without that key, Play is a black screen.`}
          {paidKey &&
            " Your paid key unlocks the full 41k catalog, enriched metadata, and debrid streams."}
        </p>
        <code className="url">{addonUrl}</code>
        <div className="row">
          <a className="install" href={stremioUrl}>Open in Stremio</a>
          <a className="secondary" href={webInstall}>Install on Stremio Web</a>
          <button className="secondary" onClick={copyAddon}>Copy link</button>
        </div>
      </div>

      <div className="card support">
        <h2>Support the hosting server</h2>
        <p>
          This addon runs on a free-tier host. If you use it, please{" "}
          <a href="https://buymeacoffee.com/borepig" target="_blank" rel="noreferrer">
            buy me a coffee
          </a>{" "}
          so the server can stay online.
        </p>
        <a
          className="coffee"
          href="https://buymeacoffee.com/borepig"
          target="_blank"
          rel="noreferrer"
        >
          Buy me a coffee
        </a>
      </div>
    </main>
  );
}

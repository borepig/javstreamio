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

const ADDON_BASE = "https://javstremio.vercel.app";

export default function Page() {
  const [service, setService] = useState("torbox");
  const [key, setKey] = useState("");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const s = localStorage.getItem(LS_SERVICE);
    const k = localStorage.getItem(LS_KEY);
    if (s && SERVICES.some((item) => item.id === s)) setService(s);
    if (k) setKey(k);
    setReady(true);
  }, []);

  useEffect(() => {
    if (!ready) return;
    localStorage.setItem(LS_SERVICE, service);
    localStorage.setItem(LS_KEY, key);
  }, [ready, service, key]);

  // Stremio requires the custom addon URL to end with /manifest.json (stremio-core
  // http_transport.rs: resource() rejects URLs that don't end with /manifest.json).
  const addonUrl = key
    ? `${ADDON_BASE}/manifest.json?service=${service}&token=${encodeURIComponent(key)}`
    : "";
  const stremioUrl = key
    ? `stremio://${ADDON_BASE.replace(/^https:\/\//, "")}/manifest.json?service=${service}&token=${encodeURIComponent(key)}`
    : "";
  const webInstall = key
    ? `https://web.stremio.com/#/addons?addon=${encodeURIComponent(addonUrl)}`
    : "";

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
      </div>

      <div className="card">
        <h2>Stremio addon link</h2>
        {key ? (
          <>
            <p>
              Uninstall any older copy first. Add this URL (Settings → Add-ons → Custom
              URL). It includes your {SERVICES.find((item) => item.id === service)?.label || service}{" "}
              key — without that key, Play is a black screen.
            </p>
            <code className="url">{addonUrl}</code>
            <div className="row">
              <a className="install" href={stremioUrl}>Open in Stremio</a>
              <a className="secondary" href={webInstall}>Install on Stremio Web</a>
              <button className="secondary" onClick={copyAddon}>Copy link</button>
            </div>
          </>
        ) : (
          <p className="warn">
            Paste your debrid API key above first. The bare site URL only lists titles.
            Clicking Play without a key opens a black screen.
          </p>
        )}
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

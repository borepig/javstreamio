"use client";
import { useEffect, useState } from "react";

const SERVICES = [
  { id: "torbox", label: "Torbox" },
  { id: "real-debrid", label: "Real-Debrid" },
  { id: "alldebrid", label: "AllDebrid" },
  { id: "torrentio", label: "Torrentio" },
  { id: "pretorx", label: "Pretorx" },
];

const LS_SERVICE = "jsio_debrid_service";
const LS_KEY = "jsio_debrid_key";

const ADDON_BASE = "https://javstreamio-debrid.vercel.app";

export default function Page() {
  const [service, setService] = useState("torbox");
  const [key, setKey] = useState("");

  useEffect(() => {
    const s = localStorage.getItem(LS_SERVICE);
    const k = localStorage.getItem(LS_KEY);
    if (s) setService(s);
    if (k) setKey(k);
  }, []);

  useEffect(() => {
    localStorage.setItem(LS_SERVICE, service);
    localStorage.setItem(LS_KEY, key);
  }, [service, key]);

  const addonUrl = key
    ? `${ADDON_BASE}?service=${service}&token=${key}`
    : ADDON_BASE;

  async function copyAddon() {
    await navigator.clipboard.writeText(addonUrl);
  }

  return (
    <main className="wrap">
      <h1>JAV StreamIO — Debrid Resolver</h1>
      <p className="sub">
        Pick a debrid service and paste your API key (stored only in this browser).
        Copy the addon link into Stremio — each title's magnet is already stored per video,
        so nothing else needs to be typed.
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
        <p>
          Add this URL to Stremio (Settings → Add-ons → Custom URL). Opening any title then
          auto-resolves its magnet through your {service} key — that's the link Stremio plays:
        </p>
        <code className="url">{addonUrl}</code>
        <div className="row">
          <button className="secondary" onClick={copyAddon}>Copy link</button>
        </div>
      </div>

      <p className="foot">
        The Stremio addon's <code>/stream</code> endpoint is dynamic on Vercel. When you open
        a title in Stremio, its <code>/stream/movie/&lt;id&gt;.json</code> resolves the torrent
        infoHash through your chosen debrid. Without a debrid, the addon still returns the magnet
        link so Stremio's built-in BitTorrent engine can play it.
      </p>
    </main>
  );
}

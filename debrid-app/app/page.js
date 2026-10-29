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
const LS_HASH = "jsio_debrid_hash";

export default function Page() {
  const [service, setService] = useState("torbox");
  const [key, setKey] = useState("");
  const [hash, setHash] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const s = localStorage.getItem(LS_SERVICE);
    const k = localStorage.getItem(LS_KEY);
    const h = localStorage.getItem(LS_HASH);
    if (s) setService(s);
    if (k) setKey(k);
    if (h) setHash(h);
  }, []);

  function persist() {
    localStorage.setItem(LS_SERVICE, service);
    localStorage.setItem(LS_KEY, key);
    localStorage.setItem(LS_HASH, hash);
  }

  async function resolve() {
    persist();
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const res = await fetch("/api/resolve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ service, key, hash }),
      });
      const data = await res.json().catch(() => ({}));
      if (data.url) {
        setResult(data.url);
      } else {
        const msg = !res.ok ? "HTTP " + res.status : "No URL returned (torrent may still be downloading)";
        setError(data.error || msg);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  async function copyUrl() {
    if (!result) return;
    await navigator.clipboard.writeText(result);
  }

  return (
    <main className="wrap">
      <h1>JAV StreamIO — Debrid Resolver</h1>
      <p className="sub">
        Pick a debrid service and paste your API key (stored only in this browser). The Stremio
        addon then resolves each video's magnet through that debrid — you don't need to type a hash.
        Optionally paste a hash/magnet to resolve a single torrent here.
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
          Torrent infoHash or magnet link <span className="hint">(optional)</span>
          <input
            type="text"
            value={hash}
            onChange={(e) => setHash(e.target.value)}
            placeholder="Optional: e.g. 5F6D... or magnet:?xt=urn:btih:..."
          />
        </label>

        <div className="row">
          <button onClick={resolve} disabled={loading || !key}>
            {loading ? "Resolving…" : "Resolve to direct URL"}
          </button>
          {result && <button className="secondary" onClick={copyUrl}>Copy URL</button>}
        </div>

        {error && <div className="err">{error}</div>}
        {result && <div className="ok">Direct URL ready — open it in your debrid app or new window.</div>}
        {result && <code className="url">{result}</code>}
      </div>

      <p className="foot">
        The Stremio addon's <code>/stream</code> endpoint is now dynamic on Vercel. When you open
        a title in Stremio, its <code>/stream/movie/&lt;id&gt;.json</code> resolves the torrent
        infoHash through your chosen debrid. Without a debrid, the addon still returns the magnet
        link so Stremio's built-in BitTorrent engine can play it.
      </p>
    </main>
  );
}

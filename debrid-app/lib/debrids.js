// Debrid service adapters: resolve a torrent infoHash (or magnet) into a direct download URL.
// Supported: Torbox, Real-Debrid, AllDebrid (documented), plus best-effort Torrentio/Pretorx.

const AGENT = "javstreamio";

async function http(method, url, { headers = {}, body = null, params = {} } = {}) {
  const u = new URL(url);
  for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v);
  const init = { method, headers: { ...headers } };
  if (body !== null) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await fetch(u, init);
  const text = await res.text();
  let parsed;
  try { parsed = JSON.parse(text); } catch { parsed = text; }
  return { ok: res.ok, status: res.status, body: parsed };
}

const getJSON = (url, opts = {}) => http("GET", url, opts);
const postJSON = (url, body, opts = {}) => http("POST", url, { ...opts, body });

// Defensively pull a download URL out of whatever shape the provider returns.
function extractUrl(resp) {
  const top = resp && resp.body !== undefined ? resp.body : resp;
  if (!top) return null;
  if (typeof top === "string") return top.startsWith("http") ? top : null;
  const data = top.data !== undefined ? top.data : top;
  if (typeof data === "string") return data.startsWith("http") ? data : null;
  if (Array.isArray(data)) {
    const first = data[0];
    if (!first) return null;
    if (typeof first === "string") return first.startsWith("http") ? first : null;
    if (first && (first.link || first.url)) return first.link || first.url;
    return null;
  }
  if (data && typeof data === "object") {
    if (data.link) return data.link;
    if (data.url) return data.url;
    if (data.downloadLink) return data.downloadLink;
    if (Array.isArray(data.links) && data.links[0]) return data.links[0];
  }
  return null;
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

function poll(fn, tries = 15, gapMs = 2000) {
  return (async () => {
    for (let i = 0; i < tries; i++) {
      const r = await fn(i);
      if (r) return r;
      await sleep(gapMs);
    }
    return null;
  })();
}

async function torbox(apiKey, infoHash, magnet) {
  const base = "https://api.torbox.app/v1/api/torrents";
  const H = { Authorization: `Bearer ${apiKey}` };
  try {
    const c = await getJSON(`${base}/checkcached`, { headers: H, params: { hash: infoHash, format: "object" } });
    const data = c.body && c.body.data ? c.body.data : {};
    const entry = data[infoHash] || data;
    if (c.ok && entry && (entry.cached || entry.isCached || entry.available)) {
      const dl = await getJSON(`${base}/requestdl`, { headers: H });
      const url = extractUrl(dl);
      if (url) return { success: true, url };
    }
    if (magnet) {
      const cr = await postJSON(`${base}/createtorrent`, { magnet }, { headers: H });
      const id = cr.body && (cr.body.data || cr.body.id);
      if (!id) return { success: false, error: "Torbox: no torrent id returned" };
      const found = await poll(async (i) => {
        const ml = await getJSON(`${base}/mylist`, { headers: H, params: { id } });
        const st = ml.body && ml.body.data;
        if (st && (st.status === "completed" || st.cached || st.downloaded)) {
          const fileId = st.fileId || st.file || "";
          const dl = await getJSON(`${base}/requestdl`, { headers: H, params: { id, fileId } });
          const url = extractUrl(dl);
          if (url) return { success: true, url };
        }
        return null;
      });
      if (found) return found;
      return { success: false, error: "Torrent still downloading on Torbox (not cached yet)" };
    }
    return { success: false, error: "Torbox: not cached and no magnet to add" };
  } catch (e) {
    return { success: false, error: "Torbox error: " + e.message };
  }
}

async function realdebrid(apiKey, infoHash, magnet) {
  const base = "https://api.real-debrid.com";
  const H = { Authorization: `Bearer ${apiKey}` };
  try {
    const avail = await getJSON(`${base}/torrents/instantAvailability/${infoHash}`, { headers: H });
    const inst = avail.body && avail.body.data && avail.body.data.instant;
    if (avail.ok && inst) {
      const link = await postJSON(`${base}/unrestrict/link`, {}, { headers: H });
      const url = extractUrl(link);
      if (url) return { success: true, url };
    }
    if (magnet) {
      const add = await postJSON(`${base}/torrents/addMagnet`, { magnet }, { headers: H });
      const id = add.body && (add.body.data || add.body.id);
      if (!id) return { success: false, error: "Real-Debrid: no torrent id returned" };
      const found = await poll(async () => {
        const info = await getJSON(`${base}/torrents/info/${id}`, { headers: H });
        const d = info.body && info.body.data;
        if (d && (d.status === "downloaded" || d.completed || d.progress === 1)) {
          const link = await postJSON(`${base}/unrestrict/link`, { id }, { headers: H });
          const url = extractUrl(link);
          if (url) return { success: true, url };
        }
        return null;
      });
      if (found) return found;
      return { success: false, error: "Torrent still downloading on Real-Debrid (not cached yet)" };
    }
    return { success: false, error: "Real-Debrid: not cached and no magnet to add" };
  } catch (e) {
    return { success: false, error: "Real-Debrid error: " + e.message };
  }
}

async function alldebrid(apiKey, infoHash, magnet) {
  const base = "https://api.alldebrid.com";
  const qp = { agent: AGENT, apikey: apiKey };
  try {
    const inst = await getJSON(`${base}/v4/instant`, { params: { ...qp, hash: infoHash } });
    const data = inst.body && inst.body.data;
    if (inst.ok && data && (data.instant || data.available)) {
      const link = await getJSON(`${base}/v4/link`, { params: { ...qp, hash: infoHash } });
      const url = extractUrl(link);
      if (url) return { success: true, url };
    }
    if (magnet) {
      const add = await postJSON(`${base}/v4/magnet`, magnet, { params: qp });
      const d2 = add.body && add.body.data;
      const id = (d2 && (d2.id || d2.torrentId)) || (add.body && add.body.id);
      if (!id) return { success: false, error: "AllDebrid: no torrent id returned" };
      const found = await poll(async () => {
        const st = await getJSON(`${base}/v4/status`, { params: { ...qp, id } });
        const d = st.body && st.body.data;
        if (d && (d.status === "completed" || d.done || d.progress === 1)) {
          const link = await getJSON(`${base}/v4/link`, { params: { ...qp, id } });
          const url = extractUrl(link);
          if (url) return { success: true, url };
        }
        return null;
      });
      if (found) return found;
      return { success: false, error: "Torrent still downloading on AllDebrid (not cached yet)" };
    }
    return { success: false, error: "AllDebrid: not cached and no magnet to add" };
  } catch (e) {
    return { success: false, error: "AllDebrid error: " + e.message };
  }
}

// --- Best-effort adapters (less documented APIs) ---

async function torrentio(apiKey, infoHash, magnet) {
  const base = "https://api.torrentio.net";
  const H = { Authorization: `Bearer ${apiKey}` };
  try {
    if (magnet) {
      const add = await postJSON(`${base}/v1/torrents`, { magnet, apiKey }, { headers: H });
      const id = add.body && (add.body.data || add.body.id);
      const link = await getJSON(`${base}/v1/torrents/${id}/link`, { headers: H });
      const url = extractUrl(link);
      if (url) return { success: true, url };
      return { success: false, error: "Torrentio: could not get link (endpoint may differ)" };
    }
    return { success: false, error: "Torrentio: not cached and no magnet to add" };
  } catch (e) {
    return { success: false, error: "Torrentio error: " + e.message };
  }
}

async function pretorx(apiKey, infoHash, magnet) {
  const base = "https://api.pretorx.com";
  const H = { Authorization: `Bearer ${apiKey}` };
  try {
    if (magnet) {
      const add = await postJSON(`${base}/v1/torrent`, { magnet, apiKey }, { headers: H });
      const id = (add.body && add.body.id) || (add.body && add.body.data && add.body.data.id);
      const link = await getJSON(`${base}/v1/torrent/${id}`, { headers: H });
      const url = extractUrl(link);
      if (url) return { success: true, url };
      return { success: false, error: "Pretorx: could not get link (endpoint may differ)" };
    }
    return { success: false, error: "Pretorx: not cached and no magnet to add" };
  } catch (e) {
    return { success: false, error: "Pretorx error: " + e.message };
  }
}

const ADAPTERS = {
  torbox,
  "real-debrid": realdebrid,
  alldebrid,
  torrentio,
  pretorx,
};

function normalizeService(s) {
  const key = String(s || "").toLowerCase().replace(/[\s-]+/g, "");
  if (key.includes("torbox")) return "torbox";
  if (key.includes("realdebrid") || key.includes("real")) return "real-debrid";
  if (key.includes("alldebrid") || key.includes("all")) return "alldebrid";
  if (key.includes("torrentio")) return "torrentio";
  if (key.includes("pretorx") || key.includes("pre")) return "pretorx";
  return null;
}

export function resolveDebrid(service, apiKey, infoHash, magnet) {
  const svc = normalizeService(service);
  const fn = svc ? ADAPTERS[svc] : null;
  if (!fn) return Promise.resolve({ success: false, error: `Unknown debrid service: ${service}` });
  return fn(apiKey, infoHash, magnet);
}

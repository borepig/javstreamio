// Debrid service adapters: resolve a torrent infoHash (or magnet) into a direct download URL.
// Supported: Torbox, Real-Debrid, AllDebrid (documented), plus best-effort Torrentio/Pretorx.

import {
  flattenAdFiles,
  hasFileId,
  magnetWithTrackers,
  matchHash,
  jobsToStop,
  parseAdInstant,
  parseCachedLookup,
  pickTorrentId,
  pickVideoFile,
  slotLimitForPlan,
  torrentRows,
  unwrapTorrent,
} from "./stream-format.js";

async function http(method, url, { headers = {}, body = null, params = {} } = {}) {
  const u = new URL(url);
  for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v);
  const init = { method, headers: { ...headers }, cache: "no-store", next: { revalidate: 0 } };
  if (body !== null) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await fetch(u, init);
  const text = await res.text();
  let parsed;
  try { parsed = JSON.parse(text); } catch { parsed = text; }
  return {
    ok: res.ok,
    status: res.status,
    body: parsed,
    total: Number(res.headers.get("x-total-count") || 0) || 0,
  };
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

async function postFormUrl(url, fields, { headers = {} } = {}) {
  const body = new URLSearchParams();
  for (const [k, v] of Object.entries(fields)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) {
      for (const item of v) body.append(k, String(item));
    } else {
      body.set(k, String(v));
    }
  }
  const res = await fetch(url, {
    method: "POST",
    headers: { ...headers, "Content-Type": "application/x-www-form-urlencoded" },
    body,
    cache: "no-store",
    next: { revalidate: 0 },
  });
  const text = await res.text();
  let parsed;
  try { parsed = text ? JSON.parse(text) : null; } catch { parsed = text; }
  return { ok: res.ok || res.status === 201 || res.status === 202 || res.status === 204, status: res.status, body: parsed };
}

async function postForm(url, fields, { headers = {} } = {}) {
  const form = new FormData();
  for (const [k, v] of Object.entries(fields)) {
    if (v === undefined || v === null || v === "") continue;
    form.append(k, String(v));
  }
  const res = await fetch(url, { method: "POST", headers, body: form, cache: "no-store", next: { revalidate: 0 } });
  const text = await res.text();
  let parsed;
  try { parsed = JSON.parse(text); } catch { parsed = text; }
  return { ok: res.ok, status: res.status, body: parsed };
}

async function torboxCdnUrl(apiKey, torrentId, fileId, clientIp) {
  if (!hasFileId(fileId)) return null;
  const params = {
    token: apiKey,
    torrent_id: String(torrentId),
    file_id: String(fileId),
  };
  if (clientIp) params.user_ip = clientIp;
  const r = await getJSON("https://api.torbox.app/v1/api/torrents/requestdl", {
    headers: { Authorization: `Bearer ${apiKey}` },
    params,
  });
  const data = r.body && r.body.data;
  if (typeof data === "string" && /^https?:\/\//i.test(data)) return data;
  const extracted = extractUrl(r);
  if (extracted && !extracted.includes("requestdl") && !extracted.includes("zip_link")) return extracted;
  return null;
}

function torrentReady(st) {
  if (!st) return false;
  if (st.download_present || st.download_finished || st.cached) return true;
  const state = String(st.download_state || "").toLowerCase();
  return state === "cached" || state === "uploading" || state === "completed";
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

async function listTorbox(base, headers) {
  const list = await getJSON(`${base}/mylist`, { headers, params: { bypass_cache: "true" } });
  return torrentRows(list.body && list.body.data);
}

async function findTorboxIdByHash(base, headers, hash) {
  if (!hash) return null;
  const hit = (await listTorbox(base, headers)).find((t) => matchHash(t, hash));
  return hit ? (hit.id ?? hit.torrent_id ?? null) : null;
}

async function controlTorbox(base, headers, id, operation) {
  if (id == null || id === "") return null;
  return postJSON(`${base}/controltorrent`, { torrent_id: id, operation }, { headers });
}

async function torboxSlotLimit(headers) {
  try {
    const me = await getJSON("https://api.torbox.app/v1/api/user/me", { headers });
    const data = me.body && me.body.data;
    if (!data) return null;
    return slotLimitForPlan(data.plan, data.additional_concurrent_slots);
  } catch {
    return null;
  }
}

async function checkTorboxCached(headers, hash) {
  if (!hash) return false;
  try {
    const r = await getJSON("https://api.torbox.app/v1/api/torrents/checkcached", {
      headers,
      params: { hash, format: "object" },
    });
    return parseCachedLookup(r.body, hash);
  } catch {
    return false;
  }
}

async function startQueuedTorbox(headers, hash) {
  if (!hash) return null;
  try {
    const queued = await getJSON("https://api.torbox.app/v1/api/queued/getqueued", {
      headers,
      params: { type: "torrent", bypass_cache: "true" },
    });
    const rows = torrentRows(queued.body && queued.body.data);
    const hit = rows.find((row) => matchHash(row, hash));
    const queuedId = hit && (hit.id ?? hit.queued_id ?? hit.torrent_id);
    if (queuedId == null || queuedId === "") return null;
    await postJSON(
      "https://api.torbox.app/v1/api/queued/controlqueued",
      { queued_id: queuedId, operation: "start" },
      { headers },
    );
    return queuedId;
  } catch {
    return null;
  }
}

async function stopOtherTorboxJobs(base, headers, rows, keepHash, limit) {
  // Official controltorrent ops are delete / resume / reannounce. pause is
  // ignored, so in-progress and seeding jobs keep the active slot.
  const targets = jobsToStop(rows, keepHash, limit).slice(0, 10);
  await Promise.all(
    targets.map((t) => controlTorbox(base, headers, t.id ?? t.torrent_id, "delete")),
  );
  return targets.length;
}

async function createTorboxTorrent(base, headers, magnetLink, keepHash, limit, { cachedOnly = false } = {}) {
  const fields = { magnet: magnetLink, allow_zip: "false", seed: "3" };
  if (cachedOnly) fields.add_only_if_cached = "true";
  let created = await postForm(`${base}/createtorrent`, fields, { headers });
  const err = created.body && created.body.error;
  if (err === "ACTIVE_LIMIT" && !cachedOnly) {
    const listed = await listTorbox(base, headers);
    await stopOtherTorboxJobs(base, headers, listed, keepHash, limit);
    created = await postForm(`${base}/createtorrent`, fields, { headers });
    if (created.body && created.body.error === "ACTIVE_LIMIT") {
      created = await postForm(
        `${base}/createtorrent`,
        { ...fields, as_queued: "true" },
        { headers },
      );
    }
  }
  return created;
}

async function loadTorboxTorrent(base, headers, torrentId) {
  if (torrentId == null) return null;
  const ml = await getJSON(`${base}/mylist`, {
    headers,
    params: { id: torrentId, bypass_cache: "true" },
  });
  return unwrapTorrent(ml.body, torrentId);
}

function torboxError(body, fallback) {
  const err = body && (body.detail || body.error);
  return err ? `Torbox: ${err}` : fallback;
}

async function torbox(apiKey, infoHash, magnet, opts = {}) {
  const base = "https://api.torbox.app/v1/api/torrents";
  const H = { Authorization: `Bearer ${apiKey}` };
  const hash = String(infoHash || "").toLowerCase();
  const magnetLink = magnetWithTrackers(magnet, hash);
  const cachedOnly = Boolean(opts.cachedOnly);
  try {
    const [rows, limit, alreadyCached] = await Promise.all([
      listTorbox(base, H),
      cachedOnly ? Promise.resolve(null) : torboxSlotLimit(H),
      checkTorboxCached(H, hash),
    ]);
    const useCachedAdd = cachedOnly || alreadyCached;
    let st = rows.find((t) => matchHash(t, hash)) || null;
    let torrentId = st ? (st.id ?? st.torrent_id ?? null) : null;
    if (!torrentId && !useCachedAdd) {
      await startQueuedTorbox(H, hash);
      torrentId = await findTorboxIdByHash(base, H, hash);
      if (torrentId) st = await loadTorboxTorrent(base, H, torrentId);
    }
    if (st && /paused/i.test(String(st.download_state || ""))) {
      await controlTorbox(base, H, torrentId, "resume");
    }
    let created = null;
    if (!torrentId && magnetLink) {
      if (!useCachedAdd) await stopOtherTorboxJobs(base, H, rows, hash, limit);
      created = await createTorboxTorrent(base, H, magnetLink, hash, limit, {
        cachedOnly: useCachedAdd,
      });
      torrentId = pickTorrentId(created.body);
      if (!torrentId) {
        await startQueuedTorbox(H, hash);
        torrentId = await findTorboxIdByHash(base, H, hash);
      }
    }
    if (!torrentId) torrentId = await findTorboxIdByHash(base, H, hash);
    if (!torrentId) {
      return { success: false, error: torboxError(created && created.body, "Torbox: could not add the magnet") };
    }
    if (!st || String(st.id ?? st.torrent_id) !== String(torrentId)) {
      st = await loadTorboxTorrent(base, H, torrentId);
    }
    return { success: true, torrentId, cached: torrentReady(st) };
  } catch (e) {
    return { success: false, error: "Torbox error: " + e.message };
  }
}

export async function resolveTorboxPlay(apiKey, { torrentId, fileId, infoHash, clientIp, cachedOnly } = {}) {
  const base = "https://api.torbox.app/v1/api/torrents";
  const H = { Authorization: `Bearer ${apiKey}` };
  const hash = String(infoHash || "").toLowerCase();
  try {
    const magnetLink = magnetWithTrackers("", hash);
    const ensured = await torbox(apiKey, hash, magnetLink, { cachedOnly: Boolean(cachedOnly) });
    let id = ensured.success ? ensured.torrentId : torrentId;
    if (!hasFileId(id) && !id) id = await findTorboxIdByHash(base, H, hash);
    if (!id && id !== 0) {
      return { success: false, error: (ensured && ensured.error) || "Torbox: missing torrent id" };
    }

    const loadTorrent = async (wantId) => {
      const ml = await getJSON(`${base}/mylist`, {
        headers: H,
        params: { id: wantId, bypass_cache: "true" },
      });
      return unwrapTorrent(ml.body, wantId);
    };

    const tryCdn = async () => {
      let currentId = id;
      let st = await loadTorrent(currentId);
      if (hash && (!st || !matchHash(st, hash))) {
        const listed = await findTorboxIdByHash(base, H, hash);
        if (listed && String(listed) !== String(currentId)) {
          currentId = listed;
          id = listed;
          st = await loadTorrent(currentId);
        }
      }
      const file = pickVideoFile((st && st.files) || []);
      const chosen = file && hasFileId(file.id) ? file.id : hasFileId(fileId) ? fileId : null;
      if (!hasFileId(chosen)) return null;
      if (st && !torrentReady(st) && !(st.files && st.files.length)) return null;
      const url = await torboxCdnUrl(apiKey, currentId, chosen, clientIp);
      return url ? { success: true, url } : null;
    };

    const found = await tryCdn();
    if (found) return { ...found, added: true, cached: true };
    const waited = await poll(tryCdn, 8, 700);
    if (waited) return { ...waited, added: true, cached: true };
    return {
      success: false,
      added: Boolean(id || id === 0),
      cached: false,
      error: "Torbox: file not ready yet — try again in a few seconds",
    };
  } catch (e) {
    return { success: false, error: "Torbox error: " + e.message };
  }
}

const RD_BASE = "https://api.real-debrid.com/rest/1.0";

export function rdIsReady(info) {
  const status = String((info && info.status) || "").toLowerCase();
  return status === "downloaded" || status === "uploading";
}

export function rdShouldServeCachingClip(played) {
  if (!played || played.url) return false;
  return Boolean(played.added) && played.cached === false;
}

export function rdTitleCode(value) {
  const raw = String(value || "").replace(/^jav:/i, "");
  const match = raw.match(/([A-Za-z]{2,8})[-_ ]?0*(\d{2,5})(?!\d)/);
  if (!match) return "";
  return `${match[1].toUpperCase()}-${String(Number(match[2]))}`;
}

export function rdRowCodes(row) {
  const text = String((row && (row.filename || row.name || row.original_filename)) || "");
  const codes = new Set();
  const re = /([A-Za-z]{2,8})[-_ ]?0*(\d{2,5})(?!\d)/g;
  let match;
  while ((match = re.exec(text))) {
    codes.add(`${match[1].toUpperCase()}-${String(Number(match[2]))}`);
  }
  return codes;
}

export function rdMatchesHash(row, hash) {
  const want = String(hash || "").toLowerCase();
  return Boolean(want && row && String(row.hash || "").toLowerCase() === want);
}

export function rdMatchesTitle(row, code) {
  const want = rdTitleCode(code);
  return Boolean(want && rdRowCodes(row).has(want));
}

export function pickRdTorrent(rows, hash, titleCode = "") {
  const matches = (Array.isArray(rows) ? rows : []).filter(
    (row) => rdMatchesHash(row, hash) || rdMatchesTitle(row, titleCode),
  );
  if (!matches.length) return null;
  return matches.find((row) => rdIsReady(row)) || matches[0];
}

export function pickRdDownload(rows, titleCode = "") {
  const want = rdTitleCode(titleCode);
  if (!want) return null;
  return (Array.isArray(rows) ? rows : []).find((row) => rdMatchesTitle(row, want)) || null;
}

export function shouldAddRdTorrent(rows, hash, titleCode = "") {
  return !pickRdTorrent(rows, hash, titleCode);
}

export function rdListRows(resp) {
  const status = resp && resp.status;
  if (!resp || status === 429 || status === 401 || status === 403) {
    return { ok: false, rows: [], status };
  }
  const body = resp.body;
  if (Array.isArray(body)) return { ok: true, rows: body, status };
  if (body && Array.isArray(body.data)) return { ok: true, rows: body.data, status };
  return { ok: false, rows: [], status };
}

export function rdAllowAdd(list, hash, titleCode = "") {
  if (!list || !list.ok) return false;
  return shouldAddRdTorrent(list.rows, hash, titleCode);
}

export function rdDuplicatesToDelete(rows, hash, keepId, titleCode = "") {
  const list = Array.isArray(rows) ? rows : [];
  const keep = list.find((row) => keepId != null && String(row.id) === String(keepId));
  const keepName = String((keep && (keep.filename || keep.name)) || "").toLowerCase();
  const keepHash = String((keep && keep.hash) || hash || "").toLowerCase();
  return list.filter((row) => {
    if (keepId != null && String(row.id) === String(keepId)) return false;
    if (rdMatchesHash(row, hash) || (keepHash && rdMatchesHash(row, keepHash))) return true;
    const name = String((row.filename || row.name) || "").toLowerCase();
    if (keepName && name && name === keepName) return true;
    return !rdIsReady(row) && rdMatchesTitle(row, titleCode);
  });
}

async function listRdTorrents(headers) {
  const list = await getJSON(`${RD_BASE}/torrents`, {
    headers,
    params: { limit: "100", page: "1" },
  });
  return rdListRows(list);
}

async function listRdDownloads(headers) {
  const list = await getJSON(`${RD_BASE}/downloads`, {
    headers,
    params: { limit: "100", page: "1" },
  });
  return rdListRows(list);
}

async function findRdOnAccount(headers, hash, titleCode = "") {
  if (!hash && !titleCode) return { ok: true, row: null, rows: [], download: null };
  const [listed, downloads] = await Promise.all([listRdTorrents(headers), listRdDownloads(headers)]);
  return {
    ok: listed.ok || downloads.ok,
    rows: listed.rows,
    row: pickRdTorrent(listed.rows, hash, titleCode),
    download: pickRdDownload(downloads.rows, titleCode),
  };
}

const rdAddLocks = new Map();

async function withRdAddLock(key, fn) {
  const wait = rdAddLocks.get(key);
  if (wait) await wait.catch(() => {});
  let release;
  const pending = new Promise((resolve) => {
    release = resolve;
  });
  rdAddLocks.set(key, pending);
  try {
    return await fn();
  } finally {
    rdAddLocks.delete(key);
    release();
  }
}

async function rdInfo(headers, id) {
  if (!id) return null;
  const info = await getJSON(`${RD_BASE}/torrents/info/${encodeURIComponent(id)}`, { headers });
  const body = info.body && typeof info.body === "object" ? info.body : null;
  if (!info.ok || !body || body.error) return null;
  return body;
}

async function rdSelectFiles(headers, id, files) {
  return postFormUrl(
    `${RD_BASE}/torrents/selectFiles/${id}`,
    { files: files || "all" },
    { headers },
  );
}

export function rdHosterLink(info) {
  return rdHosterLinks(info)[0] || null;
}

export function rdHosterLinks(info) {
  const links = ((info && info.links) || []).filter((link) => typeof link === "string" && link);
  if (!links.length) return [];
  const files = (info && info.files) || [];
  const selected = files.filter((f) => f && f.selected);
  const pool = selected.length ? selected : files;
  const video = pickVideoFile(pool);
  const ordered = [];
  if (video) {
    const idx = pool.findIndex((f) => f.id === video.id);
    if (idx >= 0 && links[idx]) ordered.push(links[idx]);
  }
  for (const link of links) {
    if (!ordered.includes(link)) ordered.push(link);
  }
  return ordered;
}

export function rdDownloadUrl(resp) {
  const top = resp && resp.body !== undefined ? resp.body : resp;
  if (!top || typeof top !== "object") return null;
  const data = top.data && typeof top.data === "object" ? top.data : top;
  const candidates = [data.download, top.download];
  for (const value of candidates) {
    if (typeof value === "string" && /^https?:\/\//i.test(value)) {
      return value.replace(/^http:\/\//i, "https://");
    }
  }
  return null;
}

export async function unlockRdLinks(apiKey, links) {
  const H = { Authorization: `Bearer ${apiKey}` };
  for (const hoster of Array.isArray(links) ? links : []) {
    const url = await rdUnrestrict(H, hoster);
    if (url) return url;
  }
  return "";
}

async function rdUnrestrict(headers, hoster) {
  if (!hoster) return null;
  // remote=1 needs paid RD sharing traffic and fails on normal accounts.
  // Try a normal unrestrict first, then remote as a fallback.
  for (const fields of [{ link: hoster }, { link: hoster, remote: "1" }]) {
    const unrestricted = await postFormUrl(`${RD_BASE}/unrestrict/link`, fields, { headers });
    const url = rdDownloadUrl(unrestricted);
    if (url) return url;
    console.error("rd-unrestrict", unrestricted.status, unrestricted.body && unrestricted.body.error);
  }
  return null;
}

function rdCached(info) {
  return rdIsReady(info);
}

async function rdDelete(headers, id) {
  if (!id) return;
  await http("DELETE", `${RD_BASE}/torrents/delete/${id}`, { headers });
}

async function rdSelectIfNeeded(headers, torrentId, info) {
  let next = info;
  if (next && next.status === "waiting_files_selection") {
    const video = pickVideoFile(next.files || []);
    await rdSelectFiles(headers, torrentId, video ? String(video.id) : "all");
    next = await rdInfo(headers, torrentId);
  }
  return next;
}

async function realdebrid(apiKey, infoHash, magnet, titleOrOpts = "") {
  const H = { Authorization: `Bearer ${apiKey}` };
  const hash = String(infoHash || "").toLowerCase();
  const titleCode = typeof titleOrOpts === "string" ? titleOrOpts : (titleOrOpts && titleOrOpts.titleCode) || "";
  const code = rdTitleCode(titleCode);
  const magnetLink = magnet || (hash ? `magnet:?xt=urn:btih:${hash}` : "");
  const lockKey = `${String(apiKey || "").slice(-8)}:${code || hash}`;
  return withRdAddLock(lockKey, async () => {
    try {
      const found = await findRdOnAccount(H, hash, code);
      if (found.download || !rdAllowAdd(found, hash, code)) {
        const existing = found.row;
        if (existing && existing.id) {
          for (const dup of rdDuplicatesToDelete(found.rows, hash, existing.id, code)) {
            await rdDelete(H, dup.id);
          }
          const info = await rdSelectIfNeeded(H, existing.id, (await rdInfo(H, existing.id)) || existing);
          return { success: true, torrentId: existing.id, cached: true, reused: true };
        }
        if (found.download) return { success: true, torrentId: "", cached: true, reused: true, download: found.download };
        return { success: false, error: "Real-Debrid: could not read torrent list" };
      }
      if (!magnetLink) return { success: false, error: "Real-Debrid: missing magnet" };
      const added = await postFormUrl(`${RD_BASE}/torrents/addMagnet`, { magnet: magnetLink }, { headers: H });
      let torrentId = (added.body && (added.body.id || (added.body.data && added.body.data.id))) || pickTorrentId(added.body);
      const again = await listRdTorrents(H);
      const ready = pickRdTorrent(again.rows, hash, code);
      if (ready && ready.id && torrentId && String(ready.id) !== String(torrentId) && rdIsReady(ready)) {
        await rdDelete(H, torrentId);
        torrentId = ready.id;
      } else if (ready && ready.id) {
        torrentId = ready.id;
      }
      if (!torrentId) return { success: false, error: "Real-Debrid: could not add the magnet" };
      for (const dup of rdDuplicatesToDelete(again.rows, hash, torrentId, code)) {
        await rdDelete(H, dup.id);
      }
      const info = await rdSelectIfNeeded(H, torrentId, (await rdInfo(H, torrentId)) || ready);
      return { success: true, torrentId, cached: rdCached(info), reused: false };
    } catch (e) {
      return { success: false, error: "Real-Debrid error: " + e.message };
    }
  });
}

export async function resolveRealDebridPlay(apiKey, { torrentId, infoHash, titleCode } = {}) {
  const H = { Authorization: `Bearer ${apiKey}` };
  const hash = String(infoHash || "").toLowerCase();
  const code = rdTitleCode(titleCode || "");
  try {
    const listed = await findRdOnAccount(H, hash, code);
    const playDownload = async (row) => {
      if (!row) return null;
      const hoster = row.link || row.generate || "";
      const url = (hoster && await rdUnrestrict(H, hoster)) || rdDownloadUrl(row) || rdDownloadUrl({ body: row });
      return url ? { success: true, url } : null;
    };
    const fromDownload = await playDownload(listed.download);
    if (fromDownload) return { ...fromDownload, added: true, cached: true };

    let id = listed.row && listed.row.id ? listed.row.id : torrentId;
    if (listed.row && listed.row.id) {
      for (const dup of rdDuplicatesToDelete(listed.rows, hash, listed.row.id, code)) {
        await rdDelete(H, dup.id);
      }
    } else if (!id && hash) {
      if (!listed.ok) return { success: false, added: false, cached: false, error: "Real-Debrid: could not read torrent list" };
      const magnet = magnetWithTrackers("", hash);
      const ensured = await realdebrid(apiKey, hash, magnet, code);
      if (ensured.download) {
        const reused = await playDownload(ensured.download);
        if (reused) return { ...reused, added: true, cached: true };
      }
      id = ensured.success ? ensured.torrentId : null;
    }
    if (!id) return { success: false, added: false, cached: false, error: "Real-Debrid: missing torrent id" };

    const unlock = async (info) => {
      if (!info) return null;
      if (info.status === "waiting_files_selection") {
        info = await rdSelectIfNeeded(H, id, info);
      }
      if (!rdIsReady(info)) return null;
      let links = rdHosterLinks(info);
      if (!links.length && (info.files || []).length) {
        await rdSelectFiles(H, id, "all");
        info = await rdInfo(H, id);
        links = rdHosterLinks(info);
      }
      const url = await unlockRdLinks(apiKey, links);
      return url ? { success: true, url } : null;
    };

    let info = await rdSelectIfNeeded(H, id, (await rdInfo(H, id)) || listed.row);
    let unlocked = await unlock(info);
    if (!unlocked) {
      unlocked = await poll(async () => {
        info = await rdSelectIfNeeded(H, id, await rdInfo(H, id));
        return unlock(info);
      }, 8, 700);
    }
    if (unlocked) return { ...unlocked, added: true, cached: true };
    return {
      success: false,
      added: true,
      cached: rdCached(info),
      error: "Real-Debrid: file not ready yet — try again in a few seconds",
    };
  } catch (e) {
    return { success: false, error: "Real-Debrid error: " + e.message };
  }
}

const AD_BASE = "https://api.alldebrid.com";
const PM_BASE = "https://www.premiumize.me/api";
const DL_BASE = "https://debrid-link.com/api/v2";

function adHeaders(apiKey) {
  return { Authorization: `Bearer ${apiKey}` };
}

function adOk(body) {
  return body && body.status === "success" && body.data;
}

function adMagnetFromUpload(body) {
  const magnets = adOk(body) && body.data.magnets;
  const first = Array.isArray(magnets) ? magnets[0] : magnets;
  if (!first || first.error) return null;
  return first.id ?? first.magnet_id ?? null;
}

async function alldebrid(apiKey, infoHash, magnet) {
  const H = adHeaders(apiKey);
  const hash = String(infoHash || "").toLowerCase();
  const magnetLink = magnet || (hash ? `magnet:?xt=urn:btih:${hash}` : "");
  try {
    if (!magnetLink) return { success: false, error: "AllDebrid: missing magnet" };
    const added = await postFormUrl(
      `${AD_BASE}/v4/magnet/upload`,
      { "magnets[]": [magnetLink] },
      { headers: H },
    );
    const torrentId = adMagnetFromUpload(added.body);
    if (!torrentId) return { success: false, error: "AllDebrid: could not add the magnet" };
    return { success: true, torrentId, cached: true };
  } catch (e) {
    return { success: false, error: "AllDebrid error: " + e.message };
  }
}

export async function resolveAllDebridPlay(apiKey, { torrentId, infoHash } = {}) {
  const H = adHeaders(apiKey);
  try {
    let id = torrentId;
    if (!id) {
      const hash = String(infoHash || "").toLowerCase();
      const ensured = await alldebrid(apiKey, hash, magnetWithTrackers("", hash));
      id = ensured.success ? ensured.torrentId : null;
    }
    if (!id) return { success: false, error: "AllDebrid: missing torrent id" };

    const tryCdn = async () => {
      const st = await postFormUrl(`${AD_BASE}/v4.1/magnet/status`, { id }, { headers: H });
      const magnets = adOk(st.body) && st.body.data.magnets;
      const mag = Array.isArray(magnets) ? magnets[0] : magnets;
      if (!mag) return null;
      const ready = mag.statusCode === 4 || String(mag.status || "").toLowerCase() === "ready";
      if (!ready) return null;
      const filesResp = await postFormUrl(`${AD_BASE}/v4/magnet/files`, { "id[]": [id] }, { headers: H });
      const fileMags = adOk(filesResp.body) && filesResp.body.data.magnets;
      const fileMag = Array.isArray(fileMags) ? fileMags.find((m) => String(m.id) === String(id)) || fileMags[0] : fileMags;
      const files = flattenAdFiles((fileMag && fileMag.files) || mag.files || []);
      const video = pickVideoFile(files);
      const hoster = (video && video.link) || (files[0] && files[0].link);
      if (!hoster) return null;
      const unlocked = await postFormUrl(`${AD_BASE}/v4/link/unlock`, { link: hoster }, { headers: H });
      const url = adOk(unlocked.body) && unlocked.body.data && unlocked.body.data.link;
      return url && String(url).startsWith("http") ? { success: true, url } : null;
    };

    let found = await tryCdn();
    if (!found) found = await poll(tryCdn, 4, 1000);
    if (found) return found;
    return { success: false, added: true, cached: false, error: "AllDebrid: file not ready yet — try again in a few seconds" };
  } catch (e) {
    return { success: false, error: "AllDebrid error: " + e.message };
  }
}

async function premiumize(apiKey, infoHash, magnet) {
  const hash = String(infoHash || "").toLowerCase();
  if (!hash && !magnet) return { success: false, error: "Premiumize: missing magnet" };
  try {
    const r = await getJSON(`${PM_BASE}/cache/check`, {
      params: { apikey: apiKey, "items[]": hash },
    });
    if (!parseCachedLookup(r.body, hash)) return { success: true, cached: false };
    return { success: true, torrentId: hash || "pm", cached: true };
  } catch (e) {
    return { success: false, error: "Premiumize error: " + e.message };
  }
}

export async function resolvePremiumizePlay(apiKey, { infoHash } = {}) {
  const hash = String(infoHash || "").toLowerCase();
  const src = hash ? `magnet:?xt=urn:btih:${hash}` : "";
  if (!src) return { success: false, error: "Premiumize: missing hash" };
  try {
    const withKey = await postFormUrl(
      `${PM_BASE}/transfer/directdl?apikey=${encodeURIComponent(apiKey)}`,
      { src },
    );
    const body = withKey.body;
    if (!body || body.status !== "success" || !Array.isArray(body.content)) {
      return { success: false, added: true, cached: false, error: "Premiumize: not cached — add the magnet in Premiumize, then retry" };
    }
    const files = body.content.map((c) => ({
      name: c.path || c.name || "",
      path: c.path || "",
      size: c.size || 0,
      link: c.link || c.stream_link,
    }));
    const video = pickVideoFile(files) || files[0];
    const url = video && video.link;
    return url && String(url).startsWith("http")
      ? { success: true, url }
      : { success: false, error: "Premiumize: no video file in the torrent" };
  } catch (e) {
    return { success: false, error: "Premiumize error: " + e.message };
  }
}

async function debridlink(apiKey, infoHash, magnet) {
  const H = { Authorization: `Bearer ${apiKey}` };
  const hash = String(infoHash || "").toLowerCase();
  const magnetLink = magnet || (hash ? `magnet:?xt=urn:btih:${hash}` : "");
  try {
    if (!magnetLink) return { success: false, error: "Debrid-Link: missing magnet" };
    const added = await postFormUrl(`${DL_BASE}/seedbox/add`, { url: magnetLink }, { headers: H });
    const value = added.body && (added.body.value || added.body.data);
    const torrentId = (value && (value.id || value._id)) || (added.body && added.body.id);
    if (!torrentId) return { success: false, error: "Debrid-Link: could not add the magnet" };
    return { success: true, torrentId };
  } catch (e) {
    return { success: false, error: "Debrid-Link error: " + e.message };
  }
}

export async function resolveDebridLinkPlay(apiKey, { torrentId, infoHash } = {}) {
  const H = { Authorization: `Bearer ${apiKey}` };
  try {
    let id = torrentId;
    if (!id) {
      const hash = String(infoHash || "").toLowerCase();
      const ensured = await debridlink(apiKey, hash, magnetWithTrackers("", hash));
      id = ensured.success ? ensured.torrentId : null;
    }
    if (!id) return { success: false, error: "Debrid-Link: missing torrent id" };

    const tryCdn = async () => {
      const list = await getJSON(`${DL_BASE}/seedbox/list`, { headers: H, params: { ids: id } });
      const value = list.body && (list.body.value || list.body.data);
      const rows = Array.isArray(value) ? value : value ? [value] : [];
      const torrent = rows.find((t) => String(t.id || t._id) === String(id)) || rows[0];
      if (!torrent) return null;
      const files = torrent.files || [];
      const ready = files.filter((f) => (f.downloadPercent == null || f.downloadPercent >= 100) && f.downloadUrl);
      const pool = ready.length ? ready : files;
      const mapped = pool.map((f) => ({
        name: f.name || f.path || "",
        path: f.name || f.path || "",
        size: f.size || 0,
        link: f.downloadUrl || f.download_url,
      }));
      const video = pickVideoFile(mapped) || mapped[0];
      const url = video && video.link;
      if (url && String(url).startsWith("http") && (torrent.downloadPercent == null || torrent.downloadPercent >= 100 || ready.length)) {
        return { success: true, url };
      }
      return null;
    };

    let found = await tryCdn();
    if (!found) found = await poll(tryCdn, 4, 1000);
    if (found) return found;
    return { success: false, added: true, cached: false, error: "Debrid-Link: file not ready yet — try again in a few seconds" };
  } catch (e) {
    return { success: false, error: "Debrid-Link error: " + e.message };
  }
}

const ADAPTERS = {
  torbox,
  "real-debrid": realdebrid,
  alldebrid,
  premiumize,
  "debrid-link": debridlink,
};

function normalizeService(s) {
  const key = String(s || "").toLowerCase().replace(/[\s-]+/g, "");
  if (key.includes("torbox")) return "torbox";
  if (key.includes("realdebrid") || key.includes("real")) return "real-debrid";
  if (key.includes("alldebrid") || key === "all") return "alldebrid";
  if (key.includes("premiumize") || key.includes("pretorx")) return "premiumize";
  if (key.includes("debridlink")) return "debrid-link";
  return null;
}

export async function lookupDebridCache(service, apiKey, infoHash, opts = {}) {
  const svc = normalizeService(service);
  const hash = String(infoHash || "").toLowerCase();
  if (!svc || !apiKey || !hash) return { cached: false };
  try {
    if (svc === "torbox") {
      return { cached: await checkTorboxCached({ Authorization: `Bearer ${apiKey}` }, hash) };
    }
    if (svc === "premiumize") {
      const r = await getJSON(`${PM_BASE}/cache/check`, {
        params: { apikey: apiKey, "items[]": hash },
      });
      return { cached: parseCachedLookup(r.body, hash) };
    }
    if (svc === "alldebrid") {
      const r = await getJSON(`${AD_BASE}/v4/magnet/instant`, {
        headers: adHeaders(apiKey),
        params: { "magnets[]": hash },
      });
      return { cached: parseAdInstant(r.body, hash) };
    }
    if (svc === "real-debrid") {
      const headers = { Authorization: `Bearer ${apiKey}` };
      const found = await findRdOnAccount(headers, hash, rdTitleCode(opts.titleCode || ""));
      const hit = found.row;
      const download = found.download;
      const links = (hit && Array.isArray(hit.links) && hit.links.length)
        ? hit.links
        : download && download.link
          ? [download.link]
          : [];
      return {
        cached: Boolean((hit && rdCached(hit)) || download),
        torrentId: hit && hit.id ? hit.id : "",
        links,
      };
    }
    if (svc === "debrid-link") {
      const headers = { Authorization: `Bearer ${apiKey}` };
      const list = await getJSON(`${DL_BASE}/seedbox/list`, { headers });
      const value = list.body && (list.body.value || list.body.data);
      const rows = Array.isArray(value) ? value : value ? [value] : [];
      const hit = rows.find((t) => String(t.hash || t.infoHash || "").toLowerCase() === hash);
      if (!hit) return { cached: false };
      const ready = hit.downloadPercent == null || hit.downloadPercent >= 100;
      return { cached: Boolean(ready) };
    }
  } catch {
    return { cached: false };
  }
  return { cached: false };
}

export function resolveDebrid(service, apiKey, infoHash, magnet, opts = {}) {
  const svc = normalizeService(service);
  const fn = svc ? ADAPTERS[svc] : null;
  if (!fn) return Promise.resolve({ success: false, error: `Unknown debrid service: ${service}` });
  return fn(apiKey, infoHash, magnet, opts);
}

export function resolvePlay(service, apiKey, opts = {}) {
  const svc = normalizeService(service);
  if (svc === "torbox") return resolveTorboxPlay(apiKey, opts);
  if (svc === "real-debrid") return resolveRealDebridPlay(apiKey, opts);
  if (svc === "alldebrid") return resolveAllDebridPlay(apiKey, opts);
  if (svc === "premiumize") return resolvePremiumizePlay(apiKey, opts);
  if (svc === "debrid-link") return resolveDebridLinkPlay(apiKey, opts);
  return Promise.resolve({ success: false, error: `No play handler for ${service}` });
}

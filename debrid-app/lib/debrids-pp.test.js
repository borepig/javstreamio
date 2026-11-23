import { test } from "node:test";
import assert from "node:assert/strict";
import { resolvePikpakPlay, lookupDebridCache } from "./debrids.js";

// infoHash from the live probe: two COMPLETED tasks shared this hash.
const HASH = "a5a89220191128a549c76eadddc4726a63164d1b";
const KEY = "pikpak-test-key";
const PP = "https://api-drive.mypikpak.com";
const FOLDER_FILES = {
  files: [{ id: 42, name: "movie.mp4", size: 5_000_000 }],
};
const FILE_DETAIL = {
  phase: "PHASE_TYPE_COMPLETE",
  medias: [{ link: { url: "http://cdn.pikpak.example/video.mp4" }}],
};

function magnetUrl(hash) {
  return `magnet:?xt=urn:btih:${hash}`;
}

function tasksBody(tasks) {
  return { tasks };
}

// Route fetch by URL prefix, most-specific first.
function mockFetch(routes) {
  const calls = [];
  const original = globalThis.fetch;
  globalThis.fetch = (url, init = {}) => {
    const u = String(url);
    calls.push({ method: init.method || "GET", url: u, body: init.body || null });
    for (const route of routes) {
      if (u.startsWith(route.url)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers(),
          text: () => Promise.resolve(JSON.stringify(route.body)),
        });
      }
    }
    return Promise.resolve({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: () => Promise.resolve(JSON.stringify({ error: { code: "NOT_FOUND" }})),
    });
  };
  return {
    calls,
    restore: () => {
      globalThis.fetch = original;
    },
  };
}

test("re-opened video resolves by task id without adding a new download", async () => {
  const tasks = [
    {
      id: "t-err",
      phase: "PHASE_TYPE_ERROR",
      file_id: "FID-ERR",
      params: { url: magnetUrl(HASH) },
    },
    {
      id: "t-done",
      phase: "PHASE_TYPE_COMPLETE",
      file_id: "FID-DONE",
      params: { url: magnetUrl(HASH) },
    },
  ];
  const { calls, restore } = mockFetch([
    { url: `${PP}/drive/v1/tasks`, body: tasksBody(tasks) },
    { url: `${PP}/drive/v1/files/`, body: FILE_DETAIL },
    { url: `${PP}/drive/v1/files?parent_id`, body: FOLDER_FILES },
  ]);
  const res = await resolvePikpakPlay(KEY, { torrentId: "t-done", infoHash: HASH });
  restore();
  assert.equal(res.success, true);
  assert.equal(res.url, "https://cdn.pikpak.example/video.mp4");
  assert.equal(res.cached, true);
  // The fix: no POST to /drive/v1/files (no duplicate offline download).
  const posts = calls.filter((c) => c.method === "POST");
  assert.equal(posts.length, 0);
});

test("hash-only lookup prefers the COMPLETED task over an errored duplicate", async () => {
  const tasks = [
    {
      id: "t-err",
      phase: "PHASE_TYPE_ERROR",
      file_id: "FID-ERR",
      params: { url: magnetUrl(HASH) },
    },
    {
      id: "t-done",
      phase: "PHASE_TYPE_COMPLETE",
      file_id: "FID-DONE",
      params: { url: magnetUrl(HASH) },
    },
  ];
  const { calls, restore } = mockFetch([
    { url: `${PP}/drive/v1/tasks`, body: tasksBody(tasks) },
    { url: `${PP}/drive/v1/files/`, body: FILE_DETAIL },
    { url: `${PP}/drive/v1/files?parent_id`, body: FOLDER_FILES },
  ]);
  const res = await resolvePikpakPlay(KEY, { infoHash: HASH });
  restore();
  // Picked the completed task's folder, not the errored duplicate.
  const listCalls = calls.filter((c) => c.url.includes("parent_id=FID-DONE"));
  assert.equal(listCalls.length, 1);
  assert.equal(res.success, true);
  assert.equal(res.cached, true);
});

test("when no task matches the hash, one new offline task is added", async () => {
  const tasks = [
    {
      id: "t-other",
      phase: "PHASE_TYPE_COMPLETE",
      file_id: "FID-OTHER",
      params: { url: magnetUrl("ffffffffffffffffffffffffffffffffffffffff") },
    },
  ];
  const { calls, restore } = mockFetch([
    { url: `${PP}/drive/v1/tasks`, body: tasksBody(tasks) },
    { url: `${PP}/drive/v1/files/`, body: FILE_DETAIL },
    { url: `${PP}/drive/v1/files?parent_id`, body: FOLDER_FILES },
    {
      url: `${PP}/drive/v1/files`,
      method: "POST",
      body: { task: { id: "t-new", file_id: "FID-NEW", phase: "PHASE_TYPE_RUNNING" } },
    },
  ]);
  const res = await resolvePikpakPlay(KEY, { infoHash: HASH });
  restore();
  const posts = calls.filter((c) => c.method === "POST");
  assert.equal(posts.length, 1);
  assert.ok(posts[0].url.startsWith(`${PP}/drive/v1/files`));
  const listCalls = calls.filter((c) => c.url.includes("parent_id=FID-NEW"));
  assert.equal(listCalls.length, 1);
  assert.equal(res.success, true);
  assert.equal(res.url, "https://cdn.pikpak.example/video.mp4");
});

test("lookupDebridCache(pikpak) reports cached only when a completed task exists", async () => {
  const withComplete = [
    {
      id: "t-done",
      phase: "PHASE_TYPE_COMPLETE",
      file_id: "FID-DONE",
      params: { url: magnetUrl(HASH) },
    },
  ];
  let { calls, restore } = mockFetch([
    { url: `${PP}/drive/v1/tasks`, body: tasksBody(withComplete) },
  ]);
  const hit = await lookupDebridCache("pikpak", KEY, HASH);
  restore();
  assert.equal(hit.cached, true);
  assert.equal(hit.fileId, "FID-DONE");
  assert.equal(hit.torrentId, "t-done");

  const withError = [
    {
      id: "t-err",
      phase: "PHASE_TYPE_ERROR",
      file_id: "FID-ERR",
      params: { url: magnetUrl(HASH) },
    },
  ];
  ({ calls, restore } = mockFetch([
    { url: `${PP}/drive/v1/tasks`, body: tasksBody(withError) },
  ]));
  const miss = await lookupDebridCache("pikpak", KEY, HASH);
  restore();
  assert.equal(miss.cached, false);
  assert.equal(miss.fileId, "FID-ERR");
});

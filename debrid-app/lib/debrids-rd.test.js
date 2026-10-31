import { test } from "node:test";
import assert from "node:assert/strict";
import {
  pickRdTorrent,
  shouldAddRdTorrent,
  rdDownloadUrl,
  rdDuplicatesToDelete,
  rdHosterLinks,
  rdIsReady,
  rdMatchesHash,
  rdTitleCode,
  rdAllowAdd,
  rdListRows,
  rdShouldServeCachingClip,
  pickRdDownload,
} from "./debrids.js";

const HASH = "833f6e52a5109818a145d2f7f3351670d5346e31";

test("caching clip only after the torrent was added and is not cached", () => {
  assert.equal(rdShouldServeCachingClip({ added: false, cached: false }), false);
  assert.equal(rdShouldServeCachingClip({ added: true, cached: true }), false);
  assert.equal(rdShouldServeCachingClip({ added: true, cached: false }), true);
  assert.equal(rdShouldServeCachingClip({ added: true, url: "https://cdn.example/file.mp4" }), false);
});

test("rdIsReady treats uploading as playable", () => {
  assert.equal(rdIsReady({ status: "downloaded" }), true);
  assert.equal(rdIsReady({ status: "uploading" }), true);
  assert.equal(rdIsReady({ status: "downloading" }), false);
  assert.equal(rdIsReady({ status: "magnet_conversion" }), false);
});

test("pickRdTorrent prefers the downloaded copy of a duplicate hash", () => {
  const rows = [
    { id: "new", hash: HASH.toUpperCase(), status: "magnet_conversion" },
    { id: "ready", hash: HASH, status: "downloaded" },
    { id: "other", hash: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", status: "downloaded" },
  ];
  const hit = pickRdTorrent(rows, HASH.toUpperCase());
  assert.equal(hit.id, "ready");
  assert.equal(rdMatchesHash(hit, HASH), true);
});

test("rdTitleCode normalizes IPZZ948 and jav ids", () => {
  assert.equal(rdTitleCode("jav:IPZZ-948"), "IPZZ-948");
  assert.equal(rdTitleCode("IPZZ948"), "IPZZ-948");
  assert.equal(rdTitleCode("ipzz00948"), "IPZZ-948");
  assert.equal(rdTitleCode("JUR-093"), "JUR-93");
});

test("shouldAddRdTorrent is false when the title is already on the list under another hash", () => {
  const rows = [
    {
      id: "theirs",
      hash: "ffffffffffffffffffffffffffffffffffffffff",
      filename: "IPZZ-948.1080p.mkv",
      status: "downloaded",
    },
  ];
  assert.equal(shouldAddRdTorrent(rows, "f5e617b947b3adc61b7c7210ad23cfe34cb56f78", "jav:IPZZ-948"), false);
  assert.equal(pickRdTorrent(rows, "f5e617b947b3adc61b7c7210ad23cfe34cb56f78", "IPZZ-948").id, "theirs");
});

test("shouldAddRdTorrent is false when the hash is already on the list", () => {
  assert.equal(shouldAddRdTorrent([], HASH), true);
  assert.equal(
    shouldAddRdTorrent([{ id: "ready", hash: HASH, status: "downloaded" }], HASH),
    false,
  );
  assert.equal(
    shouldAddRdTorrent([{ id: "pending", hash: HASH, status: "queued" }], HASH),
    false,
  );
});

test("rdDuplicatesToDelete only removes unfinished copies of the same hash", () => {
  const rows = [
    { id: "ready", hash: HASH, status: "downloaded" },
    { id: "dup", hash: HASH, status: "queued" },
    { id: "other", hash: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", status: "downloading" },
  ];
  const dups = rdDuplicatesToDelete(rows, HASH, "ready");
  assert.deepEqual(dups.map((row) => row.id), ["dup"]);
});

test("pickRdDownload treats a Downloads row as already cached", () => {
  const rows = [
    {
      id: "d1",
      filename: "489155.com@DASS-948-C.mp4",
      link: "https://real-debrid.com/d/R2VN3VOX4NND6DFA",
      download: "https://cdn.example/dass.mp4",
    },
    { id: "d2", filename: "other.mp4" },
  ];
  assert.equal(pickRdDownload(rows, "jav:DASS-948").id, "d1");
  assert.equal(pickRdDownload(rows, "IPZZ-948"), null);
});

test("DASS-948-C filename counts as DASS-948 already on the list", () => {
  const rows = [
    {
      id: "dass",
      hash: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      filename: "489155.com@DASS-948-C.mp4",
      status: "downloaded",
    },
  ];
  assert.equal(shouldAddRdTorrent(rows, "e852a613c2ddbee3b612aaca09656d2761886179", "jav:DASS-948"), false);
  assert.equal(pickRdTorrent(rows, "e852a613c2ddbee3b612aaca09656d2761886179", "DASS948").id, "dass");
});

test("rdListRows treats 429 and error objects as a failed list", () => {
  assert.equal(rdListRows({ status: 429, body: { error: "too_many_requests" } }).ok, false);
  assert.equal(rdListRows({ status: 401, body: { error: "bad_token" } }).ok, false);
  assert.equal(rdListRows({ status: 200, body: { error: "bad_token" } }).ok, false);
  assert.equal(rdListRows({ status: 200, body: [{ id: "1" }] }).ok, true);
  assert.equal(rdListRows({ status: 200, body: [{ id: "1" }] }).rows.length, 1);
});

test("rdAllowAdd is false when the torrent list could not be read", () => {
  assert.equal(rdAllowAdd({ ok: false, rows: [] }, HASH, "DASS-948"), false);
  assert.equal(rdAllowAdd({ ok: true, rows: [] }, HASH, "DASS-948"), true);
  assert.equal(
    rdAllowAdd(
      {
        ok: true,
        rows: [{ id: "dass", filename: "489155.com@DASS-948-C.mp4", status: "downloaded" }],
      },
      "e852a613c2ddbee3b612aaca09656d2761886179",
      "jav:DASS-948",
    ),
    false,
  );
});

test("rdDuplicatesToDelete removes extra finished copies of the same title", () => {
  const rows = [
    {
      id: "keep",
      hash: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      filename: "489155.com@DASS-948-C.mp4",
      status: "downloaded",
    },
    {
      id: "dup",
      hash: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      filename: "489155.com@DASS-948-C.mp4",
      status: "downloaded",
    },
    {
      id: "other",
      hash: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      filename: "JUR-093.mp4",
      status: "downloaded",
    },
  ];
  const dups = rdDuplicatesToDelete(rows, "e852a613c2ddbee3b612aaca09656d2761886179", "keep", "jav:DASS-948");
  assert.deepEqual(dups.map((row) => row.id), ["dup"]);
});

test("rdHosterLinks picks the largest selected video first", () => {
  const links = rdHosterLinks({
    files: [
      { id: 1, name: "sample.mp4", bytes: 10, selected: 1 },
      { id: 2, name: "movie.mkv", bytes: 5000, selected: 1 },
    ],
    links: ["https://real-debrid.com/d/sample", "https://real-debrid.com/d/movie"],
  });
  assert.equal(links[0], "https://real-debrid.com/d/movie");
  assert.equal(links[1], "https://real-debrid.com/d/sample");
});

test("rdDownloadUrl reads the download field only", () => {
  assert.equal(
    rdDownloadUrl({ body: { download: "https://cdn.example/file.mp4", link: "https://real-debrid.com/d/x" } }),
    "https://cdn.example/file.mp4",
  );
  assert.equal(
    rdDownloadUrl({ body: { download: "http://fc.rdeb.io/d/TFCZ/file.mp4" } }),
    "https://fc.rdeb.io/d/TFCZ/file.mp4",
  );
  assert.equal(rdDownloadUrl({ body: { error: "bad_token" } }), null);
});

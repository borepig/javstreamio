import { test } from "node:test";
import assert from "node:assert/strict";
import {
  pickTorrentId,
  torrentRows,
  matchHash,
  parseCachedLookup,
  splitSizeFlags,
  buildStreamList,
  pickVideoFile,
  playStreamUrl,
  buildAddonStreams,
  magnetWithTrackers,
  isSlotBlockingJob,
  otherSlotBlockingJobs,
  slotLimitForPlan,
  jobsToStop,
  hasFileId,
  unwrapTorrent,
  flattenAdFiles,
  serviceLabel,
  includeDebridRow,
  parseAdInstant,
  STREAM_CACHE_HINTS,
} from "./stream-format.js";

test("stream responses tell Stremio not to keep a stale cache miss", () => {
  assert.equal(STREAM_CACHE_HINTS.cacheMaxAge, 0);
  assert.equal(STREAM_CACHE_HINTS.staleRevalidate, 0);
  assert.equal(STREAM_CACHE_HINTS.staleError, 0);
});

test("pickTorrentId reads create success payload", () => {
  assert.equal(pickTorrentId({ success: true, data: { torrent_id: 42, hash: "abc" } }), 42);
});

test("pickTorrentId reads DUPLICATE_ITEM payloads", () => {
  assert.equal(
    pickTorrentId({ success: false, error: "DUPLICATE_ITEM", data: { torrent_id: 9 } }),
    9,
  );
  assert.equal(pickTorrentId({ success: false, data: { id: 11 } }), 11);
});

test("pickTorrentId rejects missing or zero ids", () => {
  assert.equal(pickTorrentId({ success: false, error: "DUPLICATE_ITEM" }), null);
  assert.equal(pickTorrentId({ data: { torrent_id: 0 } }), null);
});

test("parseCachedLookup reads Torbox object and list payloads", () => {
  const hash = "51fcbddd72b3cf01cfb8652e111cb6494c366422";
  assert.equal(parseCachedLookup({ success: true, data: { [hash]: { name: "x" } } }, hash), true);
  assert.equal(parseCachedLookup({ success: true, data: {} }, hash), false);
  assert.equal(parseCachedLookup({ success: true, data: [{ hash, cached: true }] }, hash), true);
  assert.equal(parseCachedLookup({ status: "success", response: [true] }, hash), true);
  assert.equal(parseCachedLookup({ status: "success", response: [false] }, hash), false);
});

test("otherSlotBlockingJobs deletes in-progress downloads only", () => {
  const keep = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
  const rows = [
    { id: 1, hash: keep, download_state: "downloading" },
    { id: 2, hash: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", download_state: "downloading" },
    { id: 3, hash: "cccccccccccccccccccccccccccccccccccccccc", download_state: "stalled (no seeds)" },
    { id: 4, hash: "dddddddddddddddddddddddddddddddddddddddd", download_state: "cached", cached: true },
    { id: 5, hash: "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", download_state: "paused" },
    { id: 6, hash: "ffffffffffffffffffffffffffffffffffffffff", download_state: "uploading", cached: true, download_present: true },
    { id: 7, hash: "1111111111111111111111111111111111111111", active: true },
    { id: 8, hash: "2222222222222222222222222222222222222222", download_state: "uploading", active: true },
  ];
  assert.equal(isSlotBlockingJob(rows[0]), true);
  assert.equal(isSlotBlockingJob(rows[3]), false);
  assert.equal(isSlotBlockingJob(rows[4]), false);
  assert.equal(isSlotBlockingJob(rows[5]), false);
  assert.equal(isSlotBlockingJob(rows[6]), true);
  assert.equal(isSlotBlockingJob(rows[7]), true);
  const others = otherSlotBlockingJobs(rows, keep).map((row) => row.id);
  assert.deepEqual(others, [2, 3, 7, 8]);
  assert.equal(slotLimitForPlan(1, 0), 3);
  assert.deepEqual(
    jobsToStop(rows, keep, 3).map((row) => row.id),
    [2, 3],
  );
});

test("mylist rows match hash case-insensitively", () => {
  const rows = torrentRows({
    torrents: [{ id: 7, hash: "51FCBDDD72B3CF01CFB8652E111CB6494C366422" }],
  });
  assert.equal(rows.length, 1);
  assert.ok(matchHash(rows[0], "51fcbddd72b3cf01cfb8652e111cb6494c366422"));
});

test("buildStreamList puts Torbox http first and magnet as infoHash", () => {
  const play = playStreamUrl("https://javstreamio-debrid.vercel.app", {
    service: "torbox",
    token: "k",
    torrentId: 1,
    fileId: 0,
    infoHash: "51FCBDDD72B3CF01CFB8652E111CB6494C366422",
  });
  assert.match(play, /^https:\/\/javstreamio-debrid\.vercel\.app\/play\?/);
  assert.match(play, /file_id=0/);
  assert.equal(play.includes("zip_link"), false);
  assert.equal(play.includes("requestdl"), false);
  assert.ok(hasFileId(0));

  const streams = buildStreamList({
    service: "torbox",
    debridUrl: play,
    infoHash: "51FCBDDD72B3CF01CFB8652E111CB6494C366422",
    sourceName: "5.19G 有码",
    sourceDescription: "WAAA-629",
  });
  assert.equal(streams.length, 2);
  assert.equal(streams[0].name, "Torbox\n5.19G");
  assert.equal(streams[0].description, "WAAA-629\n有码");
  assert.equal(streams[0].url, play);
  assert.equal(streams[0].url.startsWith("magnet:"), false);
  assert.equal(
    streams[0].behaviorHints.bingeGroup,
    "javstreamio-Torbox-51FCBDDD72B3CF01CFB8652E111CB6494C366422",
  );
  assert.equal(streams[1].name, "P2P\n5.19G");
  assert.equal(streams[1].infoHash, "51fcbddd72b3cf01cfb8652e111cb6494c366422");
  assert.equal(streams[1].url, undefined);
  assert.equal(streams[1].protocol, undefined);
  assert.equal(streams[1].behaviorHints.notWebReady, true);
  assert.notEqual(streams[0].behaviorHints.bingeGroup, streams[1].behaviorHints.bingeGroup);
});

test("unwrapTorrent does not return a different torrent from mylist", () => {
  const body = {
    data: [
      { id: 10, hash: "aaa", files: [{ id: 9, name: "old.mp4", size: 1 }] },
      { id: 20, hash: "bbb", files: [{ id: 0, name: "veo.mp4", size: 2 }] },
    ],
  };
  assert.equal(unwrapTorrent(body, 20).id, 20);
  assert.equal(unwrapTorrent(body, "20").files[0].name, "veo.mp4");
});

test("pickVideoFile keeps file id 0", () => {
  const file = pickVideoFile([
    { id: 0, name: "WAAA-629.mp4", size: 5000 },
    { id: 1, name: "sample.mp4", size: 10 },
  ]);
  assert.equal(file.id, 0);
});

test("pickVideoFile reads Real-Debrid path and bytes", () => {
  const file = pickVideoFile([
    { id: 1, path: "/VEO-085/sample.mp4", bytes: 10, selected: 1 },
    { id: 2, path: "/VEO-085/VEO-085.mkv", bytes: 5000, selected: 1 },
  ]);
  assert.equal(file.id, 2);
});

test("playStreamUrl can start cache from hash alone", () => {
  const play = playStreamUrl("https://javstreamio-debrid.vercel.app", {
    service: "torbox",
    token: "k",
    infoHash: "51FCBDDD72B3CF01CFB8652E111CB6494C366422",
  });
  assert.match(play, /hash=51FCBDDD72B3CF01CFB8652E111CB6494C366422/i);
  assert.equal(play.includes("torrent_id="), false);
  assert.equal(play.includes("undefined"), false);
});

test("Torbox cached row is labeled Cached and still plays via /play", () => {
  const streams = buildAddonStreams({
    origin: "https://javstreamio-debrid.vercel.app",
    service: "torbox",
    token: "k",
    infoHash: "51FCBDDD72B3CF01CFB8652E111CB6494C366422",
    sourceName: "5.19G 有码",
    sourceDescription: "WAAA-629",
    cached: true,
  });
  assert.equal(streams[0].name, "Torbox (Cached)\n5.19G");
  assert.match(streams[0].url, /[?&]cached=1/);
  assert.equal(streams[1].infoHash, "51fcbddd72b3cf01cfb8652e111cb6494c366422");
});

test("other debrids only appear when already ready", () => {
  assert.equal(includeDebridRow("torbox", false), true);
  assert.equal(includeDebridRow("torbox", true), true);
  assert.equal(includeDebridRow("real-debrid", false), true);
  assert.equal(includeDebridRow("alldebrid", true), true);
  assert.equal(includeDebridRow("premiumize", false), false);
  const skipped = buildAddonStreams({
    origin: "https://javstreamio-debrid.vercel.app",
    service: "real-debrid",
    token: "k",
    infoHash: "51FCBDDD72B3CF01CFB8652E111CB6494C366422",
    sourceName: "5.19G",
    sourceDescription: "WAAA-629",
    includeDebrid: false,
  });
  assert.equal(skipped.length, 1);
  assert.equal(skipped[0].infoHash, "51fcbddd72b3cf01cfb8652e111cb6494c366422");
});

test("parseAdInstant reads AllDebrid magnet instant payloads", () => {
  const hash = "51fcbddd72b3cf01cfb8652e111cb6494c366422";
  assert.equal(
    parseAdInstant({ status: "success", data: { magnets: [{ hash, instant: true }] } }, hash),
    true,
  );
  assert.equal(
    parseAdInstant({ status: "success", data: { magnets: [{ hash, instant: false }] } }, hash),
    false,
  );
});

test("buildAddonStreams always returns Torbox /play when a key is present", () => {
  const streams = buildAddonStreams({
    origin: "https://javstreamio-debrid.vercel.app",
    service: "torbox",
    token: "k",
    infoHash: "51FCBDDD72B3CF01CFB8652E111CB6494C366422",
    sourceName: "5.19G 有码",
    sourceDescription: "WAAA-629",
  });
  assert.equal(streams[0].name, "Torbox\n5.19G");
  assert.match(streams[0].url, /^https:\/\/javstreamio-debrid\.vercel\.app\/play\?/);
  assert.match(streams[0].url, /token=k/);
  assert.equal(streams[1].infoHash, "51fcbddd72b3cf01cfb8652e111cb6494c366422");
});

test("hash-only magnets get public trackers so Torbox can add them", () => {
  const link = magnetWithTrackers("magnet:?xt=urn:btih:51FCBDDD72B3CF01CFB8652E111CB6494C366422", "51fc");
  assert.match(link, /^magnet:\?xt=urn:btih:51FCBDDD72B3CF01CFB8652E111CB6494C366422/);
  assert.match(link, /tr=udp:\/\/tracker\.opentrackr\.org:1337\/announce/);
});

test("Real-Debrid always returns /play when a key is present", () => {
  const streams = buildAddonStreams({
    origin: "https://javstremio.vercel.app",
    service: "real-debrid",
    token: "k",
    infoHash: "51FCBDDD72B3CF01CFB8652E111CB6494C366422",
    sourceName: "5.19G 有码",
    sourceDescription: "WAAA-629",
  });
  assert.equal(streams[0].name, "Real-Debrid\n5.19G");
  assert.match(streams[0].url, /^https:\/\/javstremio\.vercel\.app\/play\?/);
  assert.match(streams[0].url, /service=real-debrid/);
  assert.match(streams[0].url, /token=k/);
  const withId = buildAddonStreams({
    origin: "https://javstremio.vercel.app",
    service: "real-debrid",
    token: "k",
    infoHash: "51FCBDDD72B3CF01CFB8652E111CB6494C366422",
    torrentId: "ABC123",
    titleCode: "jav:IPZZ-948",
    sourceName: "5.19G",
    sourceDescription: "WAAA-629",
    cached: true,
  });
  assert.match(withId[0].url, /torrent_id=ABC123/);
  assert.match(withId[0].url, /cached=1/);
  assert.match(withId[0].url, /code=jav%3AIPZZ-948/);
  const direct = buildAddonStreams({
    origin: "https://javstremio.vercel.app",
    service: "real-debrid",
    token: "k",
    infoHash: "51FCBDDD72B3CF01CFB8652E111CB6494C366422",
    sourceName: "5.19G",
    sourceDescription: "WAAA-629",
    cached: true,
    resolvedUrl: "https://fc.rdeb.io/d/TFCZ/file.mp4",
  });
  assert.equal(direct[0].url, "https://fc.rdeb.io/d/TFCZ/file.mp4");
  assert.equal(streams[1].infoHash, "51fcbddd72b3cf01cfb8652e111cb6494c366422");
});

test("Real-Debrid play URL stays on /play", () => {
  const play = playStreamUrl("https://javstreamio-debrid.vercel.app", {
    service: "real-debrid",
    token: "k",
    torrentId: "ABC123",
    infoHash: "58FC12E877999721CD09B09505045E082C13E6B2",
  });
  assert.match(play, /service=real-debrid/);
  assert.match(play, /torrent_id=ABC123/);
  assert.equal(play.includes("api.real-debrid.com"), false);
});

test("splitSizeFlags reads addon stream names", () => {
  assert.deepEqual(splitSizeFlags("5.19G 有码"), { size: "5.19G", flags: "有码" });
  assert.deepEqual(splitSizeFlags("150 min"), { size: "", flags: "150 min" });
  assert.deepEqual(splitSizeFlags("150m"), { size: "", flags: "150m" });
});

test("missing debrid key returns a setup row before P2P", () => {
  const streams = buildAddonStreams({
    origin: "https://javstremio.vercel.app",
    service: "",
    token: "",
    infoHash: "51FCBDDD72B3CF01CFB8652E111CB6494C366422",
    sourceName: "150 min",
    sourceDescription: "JUR-093",
  });
  assert.equal(streams[0].name, "Setup");
  assert.match(streams[0].externalUrl, /javstremio\.vercel\.app/);
  assert.equal(streams[1].infoHash, "51fcbddd72b3cf01cfb8652e111cb6494c366422");
  assert.equal(streams[1].name, "P2P");
  assert.equal(streams[1].behaviorHints.notWebReady, true);
});

test("flattenAdFiles walks AllDebrid nested folders", () => {
  const files = flattenAdFiles([
    { n: "folder", e: [{ n: "VEO-085.mkv", s: 100, l: "https://alldebrid.com/f/a" }] },
    { n: "sample.mp4", s: 1, l: "https://alldebrid.com/f/b" },
  ]);
  assert.equal(files.length, 2);
  assert.equal(pickVideoFile(files).name, "VEO-085.mkv");
});

test("serviceLabel covers every debrid in the picker", () => {
  assert.equal(serviceLabel("torbox"), "Torbox");
  assert.equal(serviceLabel("real-debrid"), "Real-Debrid");
  assert.equal(serviceLabel("alldebrid"), "AllDebrid");
  assert.equal(serviceLabel("premiumize"), "Premiumize");
  assert.equal(serviceLabel("debrid-link"), "Debrid-Link");
});

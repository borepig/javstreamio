import { test } from "node:test";
import assert from "node:assert/strict";
import {
  dmmPoster,
  dmmMonoPoster,
  fourhoiPoster,
  mgstagePoster,
  withPublicPosters,
  withBundledCatalogs,
  BUNDLED_CATALOGS,
  isPublicArtUrl,
  parseCatalogExtra,
} from "./addon-data.js";
import { ADDON_VERSION } from "./version.js";

test("dmmPoster splits catalog poster from fanart", () => {
  assert.equal(
    dmmPoster("jav:WAAA-629", "ps"),
    "https://pics.dmm.co.jp/digital/video/waaa00629/waaa00629ps.jpg",
  );
  assert.equal(
    dmmPoster("jav:WAAA-629", "pl"),
    "https://pics.dmm.co.jp/digital/video/waaa00629/waaa00629pl.jpg",
  );
  assert.equal(dmmPoster("jav:FC2PPV-4781367", "ps"), "");
  assert.equal(dmmPoster("jav:thread-2801881", "ps"), "");
  assert.equal(fourhoiPoster("jav:ABF-389"), "https://fourhoi.com/abf-389/cover-n.jpg");
  assert.equal(fourhoiPoster("jav:AIAV-039"), "https://fourhoi.com/aiav-039/cover-n.jpg");
  assert.equal(fourhoiPoster("jav:thread-2801881"), "");
  assert.equal(
    mgstagePoster("jav:ABF-389"),
    "https://image.mgstage.com/images/prestige/abf/389/pb_e_abf-389.jpg",
  );
  assert.equal(mgstagePoster("jav:WAAA-629"), "");
  assert.equal(mgstagePoster("jav:AIAV-039"), "");
  assert.equal(
    dmmMonoPoster("jav:DLDSS-557", "ps"),
    "https://pics.dmm.co.jp/mono/movie/adult/1dldss557/1dldss557ps.jpg",
  );
  assert.equal(dmmMonoPoster("jav:WAAA-629", "ps"), "");
});

test("withPublicPosters uses ps on catalog and pl as meta background", () => {
  const catalog = withPublicPosters(
    { metas: [{ id: "jav:WAAA-629", type: "movie", name: "WAAA-629", poster: "https://tu.djhdhs.us/x.jpg" }] },
    "catalog",
  );
  assert.equal(catalog.metas[0].poster, "https://pics.dmm.co.jp/digital/video/waaa00629/waaa00629ps.jpg");
  assert.equal(catalog.metas[0].background, undefined);

  const meta = withPublicPosters(
    { meta: { id: "jav:WAAA-629", type: "movie", name: "WAAA-629", poster: "https://tu.djhdhs.us/x.jpg" } },
    "meta",
  );
  assert.equal(meta.meta.poster, "https://pics.dmm.co.jp/digital/video/waaa00629/waaa00629ps.jpg");
  assert.equal(meta.meta.background, "https://pics.dmm.co.jp/digital/video/waaa00629/waaa00629pl.jpg");
});

test("public art rejects Vercel and forum hosts", () => {
  assert.equal(isPublicArtUrl("https://javstreamio-debrid.vercel.app/poster/movie/jav:WAAA-629"), false);
  assert.equal(isPublicArtUrl("https://tu.djhdhs.us/x.jpg"), false);
  assert.equal(isPublicArtUrl("https://pics.dmm.co.jp/digital/video/waaa00629/waaa00629ps.jpg"), true);
  assert.equal(isPublicArtUrl("https://fourhoi.com/abf-389/cover-n.jpg"), true);
  assert.equal(isPublicArtUrl("https://image.mgstage.com/images/prestige/abf/389/pb_e_abf-389.jpg"), true);
});

test("bundledMeta keeps JavDB cast and plot", async () => {
  const { bundledMeta } = await import("./addon-data.js");
  const body = bundledMeta("jav:JUR-093");
  assert.equal(body.meta.id, "jav:JUR-093");
  assert.ok(Array.isArray(body.meta.cast) && body.meta.cast.length);
  assert.ok(body.meta.cast.includes("宝生みさと"));
  assert.equal(body.meta.runtime, "150 min");
  assert.ok(Array.isArray(body.meta.genres) && body.meta.genres.includes("美乳"));
  const castLinks = (body.meta.links || []).filter((link) => link.category === "Cast");
  assert.ok(castLinks.some((link) => link.name === "宝生みさと"));
  assert.match(castLinks[0].url, /^stremio:\/\/\/search\?search=/);
  assert.equal(body.meta.description.includes("出演:"), false);
  assert.equal(body.meta.description.includes("類型:"), false);
  assert.equal(body.meta.description.includes("時長:"), false);
  assert.ok(body.meta.description.includes("无名原石妻") || body.meta.description.includes("無名の原石妻"));
  assert.ok(body.meta.imdbRating);
});

test("bundled ABF-389 uses the public MGStage cover", async () => {
  const { bundledTitle, withPublicPosters } = await import("./addon-data.js");
  const item = bundledTitle("jav:ABF-389");
  assert.equal(item.dmmMissing, true);
  assert.match(item.forumPoster || "", /djhdhs\.us/);
  const catalog = withPublicPosters(
    {
      metas: [
        {
          id: item.id,
          type: "movie",
          name: item.name,
          dmmMissing: true,
          forumPoster: item.forumPoster,
        },
      ],
    },
    "catalog",
    "https://javstreamio-debrid.vercel.app",
  );
  assert.equal(
    catalog.metas[0].poster,
    "https://image.mgstage.com/images/prestige/abf/389/pb_e_abf-389.jpg",
  );
});

test("bundled western titles keep sehuatang covers", async () => {
  const { bundledTitle, withPublicPosters } = await import("./addon-data.js");
  const item = bundledTitle("jav:thread-2801881");
  assert.ok(item);
  assert.match(item.forumPoster || "", /djhdhs\.us/);
  const catalog = withPublicPosters(
    { metas: [{ id: item.id, type: "movie", name: item.name, forumPoster: item.forumPoster }] },
    "catalog",
    "https://javstreamio-debrid.vercel.app",
  );
  assert.equal(
    catalog.metas[0].poster,
    "https://javstreamio-debrid.vercel.app/poster/movie/jav%3Athread-2801881",
  );
});

test("ABF-389 uses the public MGStage cover when DMM is now-printing", () => {
  const catalog = withPublicPosters(
    {
      metas: [
        {
          id: "jav:ABF-389",
          type: "movie",
          name: "ABF-389",
          dmmMissing: true,
          forumPoster: "https://tu.djhdhs.us/abf.jpg",
        },
      ],
    },
    "catalog",
    "https://javstreamio-debrid.vercel.app",
  );
  assert.equal(
    catalog.metas[0].poster,
    "https://image.mgstage.com/images/prestige/abf/389/pb_e_abf-389.jpg",
  );
});

test("DLDSS-557 uses the public DMM mono cover when digital is now-printing", () => {
  const catalog = withPublicPosters(
    {
      metas: [
        {
          id: "jav:DLDSS-557",
          type: "movie",
          name: "DLDSS-557",
          dmmMissing: true,
          forumPoster: "https://tu.djhdhs.us/dldss.jpg",
        },
      ],
    },
    "catalog",
    "https://javstreamio-debrid.vercel.app",
  );
  assert.equal(
    catalog.metas[0].poster,
    "https://pics.dmm.co.jp/mono/movie/adult/1dldss557/1dldss557ps.jpg",
  );
});

test("non-Prestige DMM stubs still use fourhoi", () => {
  const catalog = withPublicPosters(
    {
      metas: [
        {
          id: "jav:AIAV-039",
          type: "movie",
          name: "AIAV-039",
          dmmMissing: true,
        },
      ],
    },
    "catalog",
    "https://javstreamio-debrid.vercel.app",
  );
  assert.equal(catalog.metas[0].poster, "https://fourhoi.com/aiav-039/cover-n.jpg");
});

test("DMM stubs use fourhoi before the sehuatang poster proxy", () => {
  const catalog = withPublicPosters(
    {
      metas: [
        {
          id: "jav:NACT-147",
          type: "movie",
          name: "NACT-147",
          dmmMissing: true,
          forumPoster: "https://tu.djhdhs.us/nact.jpg",
        },
      ],
    },
    "catalog",
    "https://javstreamio-debrid.vercel.app",
  );
  assert.equal(catalog.metas[0].poster, "https://fourhoi.com/nact-147/cover-n.jpg");
  const meta = withPublicPosters(
    {
      meta: {
        id: "jav:NACT-147",
        type: "movie",
        name: "NACT-147",
        dmmMissing: true,
        forumPoster: "https://tu.djhdhs.us/nact.jpg",
      },
    },
    "meta",
    "https://javstreamio-debrid.vercel.app",
  );
  assert.equal(meta.meta.background, "https://fourhoi.com/nact-147/cover-n.jpg");
});

test("fourhoi misses still use the sehuatang poster proxy", () => {
  const catalog = withPublicPosters(
    {
      metas: [
        {
          id: "jav:ORG-017",
          type: "movie",
          name: "ORG-017",
          dmmMissing: true,
          fourhoiMissing: true,
          forumPoster: "https://tu.djhdhs.us/org.jpg",
        },
      ],
    },
    "catalog",
    "https://javstreamio-debrid.vercel.app",
  );
  assert.equal(
    catalog.metas[0].poster,
    "https://javstreamio-debrid.vercel.app/poster/movie/jav%3AORG-017",
  );
});

test("western titles keep sehuatang covers via the poster proxy", () => {
  const catalog = withPublicPosters(
    {
      metas: [
        {
          id: "jav:thread-2801881",
          type: "movie",
          name: "Innocenthigh",
          forumPoster: "https://tu.djhdhs.us/cover.jpg",
        },
      ],
    },
    "catalog",
    "https://javstreamio-debrid.vercel.app",
  );
  assert.equal(
    catalog.metas[0].poster,
    "https://javstreamio-debrid.vercel.app/poster/movie/jav%3Athread-2801881",
  );
});

test("manifest exposes the four category catalogs", () => {
  const manifest = withBundledCatalogs({ version: "0.4.5", catalogs: [{ id: "jav103", name: "old" }] });
  assert.equal(manifest.id, "community.javstremio");
  assert.equal(manifest.version, ADDON_VERSION);
  assert.equal(manifest.name, "Stremio JAV Database Addon");
  assert.match(manifest.description, new RegExp(ADDON_VERSION.replace(/\./g, "\\.")));
  assert.deepEqual(
    manifest.catalogs.map((item) => item.id),
    BUNDLED_CATALOGS.map((item) => item.id),
  );
  for (const catalog of manifest.catalogs) {
    assert.deepEqual(
      catalog.extra.map((item) => item.name),
      ["search", "skip"],
    );
  }
});

test("parseCatalogExtra reads search and skip from the Stremio extra path", () => {
  assert.deepEqual(parseCatalogExtra(""), { search: "", skip: 0 });
  assert.deepEqual(parseCatalogExtra("skip=80"), { search: "", skip: 80 });
  assert.deepEqual(parseCatalogExtra("search=WAAA"), { search: "WAAA", skip: 0 });
  assert.deepEqual(parseCatalogExtra("search=WAAA-629&skip=80"), { search: "WAAA-629", skip: 80 });
  assert.deepEqual(parseCatalogExtra("search=宝生みさと.json"), { search: "宝生みさと", skip: 0 });
});

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  dmmPoster,
  dmmMonoPoster,
  fourhoiPoster,
  fourhoiCoverUrls,
  javdbCoverUrls,
  gravureCode,
  gravureMakerPoster,
  gravureKnownCover,
  mgstagePoster,
  monoMakerPoster,
  jacketArtSources,
  needsJacketCrop,
  withPublicPosters,
  withBundledCatalogs,
  BUNDLED_CATALOGS,
  isPublicArtUrl,
  isAllowedPosterUrl,
  parseCatalogExtra,
  bundledStream,
  bundledCatalog,
  catalogExtra,
  catalogGenres,
  resolveCatalogId,
} from "./addon-data.js";
import { openTitlesDb, resolveSqlitePath, queryCatalog, querySeriesList } from "./titles-db.js";
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
  assert.equal(isPublicArtUrl("https://raw.githubusercontent.com/borepig/javstreamio/main/posters/kr/krijav-2123.jpg"), true);
});

test("public art accepts hnt SVG cards and oceanveil covers", () => {
  assert.equal(isPublicArtUrl("https://javstremio.ddns.net/posters/hnt/18yo.svg"), true);
  assert.equal(isPublicArtUrl("https://image.oceanveil.net/public/anime_titles/000/000000533/000000533_vertical_with_logo.jpg.webp"), true);
  assert.equal(isPublicArtUrl("https://hentai.tv/uploads/media/2025/10/Sister-Breeder-Episode-1.jpg"), true);
});

test("withPublicPosters keeps hnt series poster and fanart", () => {
  const seriesMeta = {
    meta: {
      id: "hnt:18yo",
      type: "series",
      name: "18yo",
      poster: "https://javstremio.ddns.net/posters/hnt/18yo.svg",
      background: "https://javstremio.ddns.net/posters/hnt/18yo.svg",
    },
  };
  const out = withPublicPosters(seriesMeta, "meta", "https://javstremio.ddns.net");
  assert.equal(out.meta.poster, "https://javstremio.ddns.net/posters/hnt/18yo.svg");
  assert.equal(out.meta.background, "https://javstremio.ddns.net/posters/hnt/18yo.svg");

  const catalog = withPublicPosters(
    { metas: [{ id: "hnt:18yo", type: "series", name: "18yo", poster: "https://javstremio.ddns.net/posters/hnt/18yo.svg" }] },
    "catalog",
    "https://javstremio.ddns.net",
  );
  assert.equal(catalog.metas[0].poster, "https://javstremio.ddns.net/posters/hnt/18yo.svg");
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
    "https://javstreamio-debrid.vercel.app/poster/movie/jav%3AABF-389",
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

test("ABF-389 crops the jacket instead of using the landscape MGStage scan", () => {
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
    "https://javstreamio-debrid.vercel.app/poster/movie/jav%3AABF-389",
  );
});

test("DLDSS-557 uses the FourHoi cover (proxied) when DMM is now-printing", () => {
  // fourhoi.com 403s on any Referer, and the Stremio client sends one with
  // every image fetch — so catalog/meta responses must carry the /poster
  // proxy URL (server-side fetch sends no Referer) instead of the direct
  // fourhoi URL.
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
    "https://javstreamio-debrid.vercel.app/poster/movie/jav%3ADLDSS-557",
  );
});

test("non-Prestige DMM stubs still use fourhoi (proxied)", () => {
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
  assert.equal(
    catalog.metas[0].poster,
    "https://javstreamio-debrid.vercel.app/poster/movie/jav%3AAIAV-039",
  );
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
  assert.equal(
    catalog.metas[0].poster,
    "https://javstreamio-debrid.vercel.app/poster/movie/jav%3ANACT-147",
  );
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
  assert.equal(meta.meta.poster, "https://javstreamio-debrid.vercel.app/poster/movie/jav%3ANACT-147");
  assert.equal(
    meta.meta.background,
    "https://javstreamio-debrid.vercel.app/poster/movie/jav%3ANACT-147?fanart=1",
  );
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

test("jacket prefixes use a right-side fanart crop as the poster", () => {
  const meta = withPublicPosters(
    {
      meta: {
        id: "jav:SILK-052",
        type: "movie",
        name: "SILK-052",
        poster: "https://pics.dmm.co.jp/digital/video/silk00052/silk00052ps.jpg",
        background: "https://pics.dmm.co.jp/digital/video/silk00052/silk00052pl.jpg",
        dmmMissing: true,
      },
    },
    "meta",
    "https://javstremio.ddns.net",
  );
  assert.equal(meta.meta.poster, "https://javstremio.ddns.net/poster/movie/jav%3ASILK-052");
  assert.equal(
    meta.meta.background,
    "https://javstremio.ddns.net/poster/movie/jav%3ASILK-052?fanart=1",
  );

  const cropped = withPublicPosters(
    {
      metas: [
        {
          id: "jav:ABF-385",
          type: "movie",
          name: "ABF-385",
          poster: "https://javstremio.ddns.net/posters/javcensored/jav:ABF-385.jpg",
          dmmMissing: true,
        },
      ],
    },
    "catalog",
    "https://javstremio.ddns.net",
  );
  assert.equal(cropped.metas[0].poster, "https://javstremio.ddns.net/poster/movie/jav%3AABF-385");
});

test("Japan Photo gravure codes use a right-side jacket crop instead of the DMM stub", () => {
  assert.deepEqual(gravureCode("REBDB-343 Yukina Virgin snow"), {
    prefix: "rebdb",
    digits: "343",
    suffix: "",
  });
  assert.deepEqual(gravureCode("KIDM-658B 星野美優"), {
    prefix: "kidm",
    digits: "658",
    suffix: "b",
  });
  assert.equal(fourhoiCoverUrls("REBDB-343 Yukina")[0], "https://fourhoi.com/rebdb-343/cover-n.jpg");
  assert.deepEqual(fourhoiCoverUrls("SZKK-0027 地下偶像"), [
    "https://fourhoi.com/szkk-0027/cover-n.jpg",
    "https://fourhoi.com/szkk-027/cover-n.jpg",
    "https://fourhoi.com/szkk-27/cover-n.jpg",
  ]);
  assert.equal(fourhoiCoverUrls("PPTGB-010 放課後")[0], "https://fourhoi.com/pptgb-010/cover-n.jpg");
  assert.equal(fourhoiCoverUrls("R-656 天使")[0], "https://fourhoi.com/r-656/cover-n.jpg");
  assert.equal(fourhoiCoverUrls("KIDM-658B Milk Girl")[0], "https://fourhoi.com/kidm-658b/cover-n.jpg");
  assert.equal(
    javdbCoverUrls("REBDB-343 Yukina")[0],
    "https://c0.jdbstatic.com/covers/re/rebdb-343.jpg",
  );
  assert.deepEqual(javdbCoverUrls("R-656 天使"), []);

  const item = {
    id: "hk:cat3-1",
    type: "movie",
    category: "japan-photo",
    name: "REBDB-343 Yukina Virgin snow",
    poster: "https://pics.dmm.co.jp/digital/video/rebdb00343/rebdb00343ps.jpg",
    background: "https://pics.dmm.co.jp/digital/video/rebdb00343/rebdb00343pl.jpg",
  };
  assert.equal(needsJacketCrop(item), true);
  assert.equal(needsJacketCrop({ ...item, category: "japan", name: "REBD-634 Riko" }), false);
  const sources = jacketArtSources(item);
  assert.equal(
    sources[0],
    "https://pics.dmm.co.jp/digital/video/h_346rebdb00343/h_346rebdb00343pl.jpg",
  );
  assert.equal(sources[1], "https://fourhoi.com/rebdb-343/cover-n.jpg");
  assert.equal(sources.includes(item.poster), false);
  assert.equal(
    gravureMakerPoster("R-656 天使-桃乃木かな"),
    "https://pics.dmm.co.jp/digital/video/h_1143r00656/h_1143r00656pl.jpg",
  );
  assert.equal(
    gravureMakerPoster("MW-003 富永綾"),
    "https://pics.dmm.co.jp/digital/video/h_1265mw00003/h_1265mw00003pl.jpg",
  );
  assert.equal(
    gravureKnownCover("PROU-027 梅田ののか"),
    "https://c0.jdbstatic.com/covers/w1/W1b68.jpg",
  );
  assert.equal(
    jacketArtSources({ ...item, name: "PROU-027 梅田ののか" })[0],
    "https://c0.jdbstatic.com/covers/w1/W1b68.jpg",
  );
  assert.equal(
    jacketArtSources({ ...item, name: "2018年11月2日 最新 maraa-026 A*" })[0],
    "https://c0.jdbstatic.com/covers/pk/pkxXB.jpg",
  );
  assert.equal(
    jacketArtSources({ ...item, name: "AP-020B 大槻ひびき" })[0],
    "https://c0.jdbstatic.com/covers/8v/8V4OW.jpg",
  );
  assert.equal(
    jacketArtSources({ ...item, name: "BTHA-024B 尾上若葉" })[0],
    "https://c0.jdbstatic.com/covers/dk/DKqkp.jpg",
  );
  assert.equal(sources.includes(item.background), false);

  const catalog = withPublicPosters(
    { metas: [item] },
    "catalog",
    "https://javstremio.ddns.net",
  );
  assert.equal(catalog.metas[0].poster, "https://javstremio.ddns.net/poster/movie/hk%3Acat3-1");
  assert.equal(catalog.metas[0].background, undefined);
  assert.equal(catalog.metas[0].category, undefined);

  const meta = withPublicPosters({ meta: item }, "meta", "https://javstremio.ddns.net");
  assert.equal(meta.meta.poster, "https://javstremio.ddns.net/poster/movie/hk%3Acat3-1");
  assert.equal(meta.meta.background, "https://javstremio.ddns.net/poster/movie/hk%3Acat3-1?fanart=1");
});

test("DLDSS and ORG use a jacket crop instead of the tiny or missing poster", () => {
  const dldss = withPublicPosters(
    {
      metas: [
        {
          id: "jav:DLDSS-584",
          type: "movie",
          name: "DLDSS-584",
          poster: "https://pics.dmm.co.jp/mono/movie/adult/1dldss584/1dldss584ps.jpg",
        },
      ],
    },
    "catalog",
    "https://javstremio.ddns.net",
  );
  assert.equal(dldss.metas[0].poster, "https://javstremio.ddns.net/poster/movie/jav%3ADLDSS-584");

  assert.equal(
    monoMakerPoster("jav:ORG-017"),
    "https://pics.dmm.co.jp/mono/movie/adult/h_771org017/h_771org017pl.jpg",
  );
  const sources = jacketArtSources({
    id: "jav:ORG-017",
    fourhoiMissing: true,
    forumPoster: "https://tu.djhdhs.us/org-screens.jpg",
    dmmMissing: true,
  });
  assert.equal(sources[0], monoMakerPoster("jav:ORG-017"));
  assert.equal(needsJacketCrop({ id: "jav:HEZ-913", dmmMissing: true }), true);
  assert.equal(needsJacketCrop({ id: "jav:WAAA-629" }), false);

  // Probe left dmm_missing=0, but the digital CID is the gray NOW PRINTING stub.
  const jura = withPublicPosters(
    {
      metas: [
        {
          id: "jav:JURA-212",
          type: "movie",
          name: "JURA-212",
          poster: "https://pics.dmm.co.jp/digital/video/jura00212/jura00212ps.jpg",
          background: "https://pics.dmm.co.jp/digital/video/jura00212/jura00212pl.jpg",
        },
      ],
    },
    "catalog",
    "https://javstremio.ddns.net",
  );
  assert.equal(jura.metas[0].poster, "https://javstremio.ddns.net/poster/movie/jav%3AJURA-212");
  assert.equal(needsJacketCrop({ id: "jav:JRZE-320" }), true);
  assert.equal(needsJacketCrop({ id: "jav:MXGS-1454" }), true);
  const mxgs = withPublicPosters(
    {
      metas: [
        {
          id: "jav:MXGS-1454",
          type: "movie",
          name: "MXGS-1454",
          poster: "https://pics.dmm.co.jp/digital/video/mxgs01454/mxgs01454ps.jpg",
        },
      ],
    },
    "catalog",
    "https://javstremio.ddns.net",
  );
  assert.equal(mxgs.metas[0].poster, "https://javstremio.ddns.net/poster/movie/jav%3AMXGS-1454");
});

test("jacket prefix with a real DMM poster keeps that poster", () => {
  const catalog = withPublicPosters(
    {
      metas: [
        {
          id: "jav:NSPS-829",
          type: "movie",
          name: "NSPS-829",
          poster: "https://pics.dmm.co.jp/digital/video/nsps00829/nsps00829ps.jpg",
          background: "https://pics.dmm.co.jp/digital/video/nsps00829/nsps00829pl.jpg",
        },
      ],
    },
    "catalog",
    "https://javstremio.ddns.net",
  );
  assert.equal(
    catalog.metas[0].poster,
    "https://pics.dmm.co.jp/digital/video/nsps00829/nsps00829ps.jpg",
  );
});

test("NOW PRINTING rows: stored DMM poster/background are skipped (FSDSS)", () => {
  // Regression: the DB stores the DMM digital URL in poster/background for
  // NOW PRINTING titles (gray "NOW PRINTING" stub). With dmm_missing=1 the
  // stored DMM art must be skipped so the fourhoi cover is used instead.
  const catalog = withPublicPosters(
    {
      metas: [
        {
          id: "jav:FSDSS-007",
          type: "movie",
          name: "FSDSS-007",
          poster: "https://pics.dmm.co.jp/digital/video/fsdss00007/fsdss00007ps.jpg",
          dmmMissing: true,
        },
      ],
    },
    "catalog",
    "https://javstreamio.ddns.net",
  );
  assert.equal(
    catalog.metas[0].poster,
    "https://javstreamio.ddns.net/poster/movie/jav%3AFSDSS-007",
  );

  const meta = withPublicPosters(
    {
      meta: {
        id: "jav:FSDSS-007",
        type: "movie",
        name: "FSDSS-007",
        poster: "https://pics.dmm.co.jp/digital/video/fsdss00007/fsdss00007ps.jpg",
        background: "https://pics.dmm.co.jp/digital/video/fsdss00007/fsdss00007pl.jpg",
        dmmMissing: true,
      },
    },
    "meta",
    "https://javstreamio.ddns.net",
  );
  assert.equal(
    meta.meta.poster,
    "https://javstreamio.ddns.net/poster/movie/jav%3AFSDSS-007",
  );
  assert.equal(
    meta.meta.background,
    "https://javstreamio.ddns.net/poster/movie/jav%3AFSDSS-007?fanart=1",
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

test("manifest exposes the category catalogs with filter-chip options", () => {
  const manifest = withBundledCatalogs({ version: "0.4.5", catalogs: [{ id: "jav103", name: "old" }] });
  assert.equal(manifest.id, "community.javstremio");
  assert.equal(manifest.version, ADDON_VERSION);
  assert.equal(manifest.name, "Stremio JAV Database Addon");
  assert.match(manifest.description, new RegExp(ADDON_VERSION.replace(/\./g, "\\.")));
  assert.deepEqual(
    manifest.catalogs.map((item) => item.id),
    BUNDLED_CATALOGS.map((item) => item.id),
  );
  const byId = new Map(BUNDLED_CATALOGS.map((c) => [c.id, c]));
  // JAV base catalogs: the genre extra carries the top-30 genre options
  // (computed from titles.sqlite) so the Stremio client renders chips.
  const censored = byId.get("jav-censored");
  assert.deepEqual(censored.extra.map((e) => e.name), ["search", "skip", "genre"]);
  const censoredGenre = censored.extra.find((e) => e.name === "genre");
  assert.equal(censoredGenre.options.length, 30);
  assert.equal(censoredGenre.optionsLimit, 1);
  assert.deepEqual(censored.genres, censoredGenre.options);
  // Year catalogs follow the Cinemeta pattern: the genre extra's options are
  // release years, and the selection is required.
  const censoredYear = byId.get("jav-censored-year");
  assert.deepEqual(censoredYear.extra.map((e) => e.name), ["genre", "skip"]);
  const censoredYearExtra = censoredYear.extra.find((e) => e.name === "genre");
  assert.equal(censoredYearExtra.isRequired, true);
  assert.equal(censoredYearExtra.options.length, 22);
  assert.deepEqual(censoredYear.extraSupported, ["genre", "skip"]);
  assert.deepEqual(censoredYear.extraRequired, ["genre"]);
  assert.deepEqual(censoredYear.genres, censoredYearExtra.options);
  const uncensoredYear = byId.get("jav-uncensored-year");
  assert.equal(uncensoredYear.extra.find((e) => e.name === "genre").options.length, 22);
  // Non-JAV catalogs keep the bare search/skip/genre/year extras.
  const western = byId.get("western");
  assert.deepEqual(western.extra.map((e) => e.name), ["search", "skip", "genre", "year"]);
});

test("year catalogs resolve to their base category", () => {
  assert.equal(resolveCatalogId("jav-censored-year"), "jav-censored");
  assert.equal(resolveCatalogId("jav-uncensored-year"), "jav-uncensored");
  assert.equal(resolveCatalogId("jav103"), "jav-censored");
  assert.equal(resolveCatalogId("western"), "western");
});

test("catalogExtra and catalogGenres expose per-catalog filter options", () => {
  assert.deepEqual(
    catalogExtra("jav-censored").map((e) => e.name),
    ["search", "skip", "genre"],
  );
  assert.deepEqual(catalogGenres("jav-censored"), catalogExtra("jav-censored").find((e) => e.name === "genre").options);
  assert.deepEqual(
    catalogExtra("jav-censored-year").map((e) => e.name),
    ["genre", "skip"],
  );
  assert.deepEqual(catalogGenres("jav-censored-year"), catalogExtra("jav-censored-year").find((e) => e.name === "genre").options);
  assert.equal(catalogGenres("western"), undefined);
});

test("bundledCatalog treats the genre param as a year for year catalogs", () => {
  const file = resolveSqlitePath();
  if (!file) return;
  // Year catalog: the client sends the selected year via the `genre` param.
  const yearResult = bundledCatalog("jav-censored-year", 0, 15, "", "2024", "");
  assert.ok(yearResult.metas.length > 0, "expected 2024 titles in the censored year catalog");
  for (const m of yearResult.metas) {
    assert.ok(
      String(m.releaseInfo || "").includes("2024") || String(m.released || "").startsWith("2024"),
      `${m.id} must be a 2024 title`
    );
  }
  const empty = bundledCatalog("jav-censored-year", 0, 15, "", "1999", "");
  assert.equal(empty.metas.length, 0, "no 1999 censored titles expected");
  // A 4-digit `genre` value on the base JAV catalog also acts as a year filter.
  const asYear = bundledCatalog("jav-censored", 0, 3, "", "2024", "");
  for (const m of asYear.metas) {
    assert.ok(
      String(m.releaseInfo || "").includes("2024") || String(m.released || "").startsWith("2024"),
      `${m.id} must be a 2024 title`
    );
  }
});

test("parseCatalogExtra reads search, skip, genre and year from the Stremio extra path", () => {
  assert.deepEqual(parseCatalogExtra(""), { search: "", skip: 0, genre: "", year: "" });
  assert.deepEqual(parseCatalogExtra("skip=80"), { search: "", skip: 80, genre: "", year: "" });
  assert.deepEqual(parseCatalogExtra("search=WAAA"), { search: "WAAA", skip: 0, genre: "", year: "" });
  assert.deepEqual(parseCatalogExtra("search=WAAA-629&skip=80"), { search: "WAAA-629", skip: 80, genre: "", year: "" });
  assert.deepEqual(parseCatalogExtra("search=宝生みさと.json"), { search: "宝生みさと", skip: 0, genre: "", year: "" });
  assert.deepEqual(parseCatalogExtra("genre=美乳&year=2019"), { search: "", skip: 0, genre: "美乳", year: "2019" });
});

test("queryCatalog filters by genre and year", () => {
  const file = resolveSqlitePath();
  if (!file) {
    return;
  }
  const db = openTitlesDb(file);
  const byGenre = queryCatalog(db, { catalogId: "jav-censored", page: 3, genre: "美乳" });
  assert.ok(byGenre.length > 0, "expected at least 3 titles with genre 美乳");
  for (const t of byGenre) {
    assert.ok((t.genres || []).includes("美乳"), `${t.id} must carry genre 美乳`);
  }
  const byYear = queryCatalog(db, { catalogId: "jav-censored", page: 3, year: "2024" });
  assert.ok(byYear.length > 0, "expected at least 3 titles released in 2024");
  for (const t of byYear) {
    const yearOk = String(t.released || "").startsWith("2024") || String(t.releaseInfo || "").includes("2024");
    assert.ok(yearOk, `${t.id} must be a 2024 title`);
  }
  const none = queryCatalog(db, { catalogId: "jav-censored", page: 3, year: "1999" });
  assert.equal(none.length, 0, "no 1999 titles expected in the recent censored set");
});

test("querySeriesList filters hnt series by genre and year", () => {
  const file = resolveSqlitePath();
  if (!file) return;
  const db = openTitlesDb(file);
  const all = querySeriesList(db, { category: "hentai", limit: 3 });
  assert.ok(all.length > 0, "hentai series list should not be empty");
  const byGenre = querySeriesList(db, { category: "hentai", limit: 3, genre: "OVA" });
  const byYear = querySeriesList(db, { category: "hentai", limit: 3, year: "2025" });
  // OVA / 2025 may simply have no matches in the top of the list — the
  // filters must not throw and must return a valid (possibly empty) page.
  assert.ok(Array.isArray(byGenre) && Array.isArray(byYear));
  void all;
});

test("current sehuatang mirror is allowed so western covers proxy", () => {
  assert.equal(
    isAllowedPosterUrl("https://tu.zq1by.com/tupian/forum/202611/27/051159czyl9yll8fofcd6c.jpg"),
    true,
  );
  const catalog = withPublicPosters(
    {
      metas: [
        {
          id: "jav:Vixen.26.11.13.Christy.White.And.Avrora.Meey",
          type: "movie",
          name: "Vixen",
          forumPoster: "https://tu.zq1by.com/tupian/forum/202611/27/051159czyl9yll8fofcd6c.jpg",
        },
      ],
    },
    "catalog",
    "https://javstremio.ddns.net",
  );
  assert.equal(
    catalog.metas[0].poster,
    "https://javstremio.ddns.net/poster/movie/jav%3AVixen.26.11.13.Christy.White.And.Avrora.Meey",
  );
});

test("hanime-cdn posters are public art and allowed for the poster proxy", () => {
  assert.equal(isPublicArtUrl("https://hanime-cdn.com/covers/series-cover.jpg"), true);
  assert.equal(isAllowedPosterUrl("https://hanime-cdn.com/covers/series-cover.jpg"), true);
});

test("douban posters are public art and allowed for the poster proxy", () => {
  const douban = "https://img3.doubanio.com/view/photo/s_ratio_poster/public/p2911053327.jpg";
  assert.equal(isPublicArtUrl(douban), true);
  assert.equal(isAllowedPosterUrl(douban), true);
  const liveCard = "https://javstremio.ddns.net/posters/hkcat3/hk-cat3-5.svg";
  assert.equal(isPublicArtUrl(liveCard), true);
  assert.equal(isAllowedPosterUrl(liveCard), true);
});

test("GitHub raw posters (Chinese/Korean covers) allowed for the poster proxy", () => {
  assert.equal(
    isAllowedPosterUrl("https://raw.githubusercontent.com/borepig/javstreamio/main/posters/kr/krijav-2123.jpg"),
    true
  );
});

test("hnt episodes keep the P2P infoHash row but skip the CF-protected Online row", (t) => {
  const file = resolveSqlitePath();
  if (!file) {
    t.skip("titles.sqlite not present");
    return;
  }
  const db = openTitlesDb(file);
  // Episode-ref rows only (hnt:<key>:<n>); legacy hnt:nyaa-* rows keep the Online row.
  const row = db
    .prepare("SELECT id FROM titles WHERE id GLOB 'hnt:*:*' AND stream_url != '' AND info_hash != '' LIMIT 1")
    .get();
  if (!row) {
    t.skip("no hnt episode rows have stream_url + info_hash yet");
    return;
  }
  const body = bundledStream(row.id);
  const streams = (body && body.streams) || [];
  assert.equal(streams.find((s) => s.url), undefined, "hnt episodes must not emit the Online row");
  const p2p = streams.find((s) => s.infoHash);
  assert.ok(p2p, "expected the P2P infoHash row to remain");
});

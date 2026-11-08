// Throwaway probe: does the Lulustream/underhentai chain work server-side?
// Chain: underhentai series page -> /watch/?id&ep -> luluvdo.com/e/<token> -> m3u8
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36";

async function get(url, referer) {
  const headers = { "User-Agent": UA, "Accept": "text/html,application/json" };
  if (referer) headers["Referer"] = referer;
  const res = await fetch(url, { headers, redirect: "follow" });
  const text = await res.text();
  return { status: res.status, text, url: res.url };
}

const SLUG = process.argv[2] || "pet-life";

// 1) underhentai series page
const page1 = await get(`https://www.underhentai.net/${SLUG}/`);
console.log(`[1] underhentai /${SLUG}/ -> ${page1.status}`);
const page1Text = page1.text.replace(/&amp;/g, "&");
const watchRe = /href="\/watch\/\?id=(\d+)&amp;ep=(\d+)"/.source;
const wm = page1Text.match(/\/watch\/\?id=(\d+)&ep=(\d+)/);
if (!wm) { console.log("  no /watch link found (series may not exist / slug wrong)"); process.exit(0); }
const id = wm[1], ep = wm[2];
console.log(`  watch link: id=${id} ep=${ep}`);

// 2) underhentai watch page -> luluvdo iframe token
const page2 = await get(`https://www.underhentai.net/watch/?id=${id}&ep=${ep}`, `https://www.underhentai.net/${SLUG}/`);
console.log(`[2] underhentai /watch -> ${page2.status}`);
const ifRe = /<iframe[^>]*src="(https:\/\/luluvdo\.com\/e\/[a-z0-9]+)"/;
const im = page2.text.match(ifRe);
const luluUrl = im ? im[1] : null;
console.log("  luluvdo iframe:", luluUrl || "(none)");
if (!luluUrl) { console.log("  no luluvdo iframe found"); process.exit(0); }

// 3) luluvdo page -> find m3u8 (it's set by JS, so also look for a direct media var)
const page3 = await get(luluUrl, "https://www.underhentai.net/watch/");
console.log(`[3] ${luluUrl} -> ${page3.status}`);
const m3u8Re = /(https?:\/\/[a-z0-9.]*cdn-tnmr\.org\/hls2\/[^\s"']+\.m3u8[^\s"']*)/;
const m = page3.text.match(m3u8Re);
const m3u8 = m ? m[1] : null;
console.log("  m3u8 in HTML:", m3u8 || "(not in static HTML - JS-rendered)");

// 4) if we found a m3u8, try to fetch it
if (m3u8) {
  const r4 = await fetch(m3u8, { headers: { "User-Agent": UA, "Referer": luluUrl } });
  const body = await r4.text();
  console.log(`[4] m3u8 fetch -> ${r4.status}`);
  console.log("  body head:", body.slice(0, 200).replace(/\n/g, " | "));
}
console.log("PROBE DONE");

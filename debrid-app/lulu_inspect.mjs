// Inspect how the luluvdo page sets the JWPlayer media source (m3u8).
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36";
(async () => {
  const res = await fetch("https://luluvdo.com/e/q984z88m3llp", { headers: { "User-Agent": UA } });
  const t = await res.text();
  for (const kw of ["m3u8", "jwplayer", "sources", "mediaUrl", "player.start", ".mp4", "file", "cdn-tnmr"]) {
    const re = new RegExp(kw, "gi");
    const ms = [...t.matchAll(re)];
    if (ms.length) {
      const i = t.toLowerCase().indexOf(kw.toLowerCase());
      console.log(`[${kw}] count=${ms.length} :: ...${t.slice(Math.max(0, i - 50), i + 140).replace(/\s+/g, " ")}...`);
    } else {
      console.log(`[${kw}] not found`);
    }
  }
})();

// Extract the pipe-delimited m3u8 parts from the luluvdo page.
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36";
function short(p) {
  if (p.length > 40) { return p.slice(0, 40) + "..."; }
  return p;
}
(async () => {
  const res = await fetch("https://luluvdo.com/e/q984z88m3llp", { headers: { "User-Agent": UA } });
  const t = await res.text();
  const m = t.match(/'([^']*hls2[^']*)'\.split\('\|'\)/);
  if (!m) { console.log("no pipe string found"); return; }
  const parts = m[1].split("|");
  console.log("PARTS (" + parts.length + "):");
  for (let i = 0; i < parts.length; i++) {
    console.log(i + ": " + short(parts[i]));
  }
})();

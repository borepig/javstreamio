// Locate the signature token and numeric tokens (timestamp vs file id) in the pipe string.
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36";
(async () => {
  const res = await fetch("https://luluvdo.com/e/q984z88m3llp", { headers: { "User-Agent": UA } });
  const t = await res.text();
  const m = t.match(/'([^']*hls2[^']*)'\s*\.split\('\|'\)/);
  const P = m[1].split("|");
  console.log("sig candidates (len>=30):");
  P.forEach((v, i) => { if (v.length >= 30) console.log(i + ": " + (v.length > 50 ? v.slice(0, 50) + "..." : v) + " [len=" + v.length + "]"); });
  console.log("numeric tokens:");
  P.forEach((v, i) => { if (/^\d+$/.test(v)) console.log(i + ": " + v); });
})();

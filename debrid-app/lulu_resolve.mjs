// Robust Lulustream m3u8 resolver: parse the pipe string by token, build the URL, fetch it.
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36";

// Find the first part index whose value matches the given predicate.
function findIdx(P, pred) {
  for (let i = 0; i < P.length; i++) {
    if (pred(P[i])) return i;
  }
  return -1;
}

(async () => {
  const token = process.argv[2] || "q984z88m3llp";
  const res = await fetch(`https://luluvdo.com/e/${token}`, {
    headers: { "User-Agent": UA, "Referer": "https://www.underhentai.net/" },
  });
  const t = await res.text();
  const m = t.match(/'([^']*hls2[^']*)'\.split\('\|'\)/);
  if (!m) { console.log("no pipe string"); return; }
  const P = m[1].split("|");

  // Token-based component lookup (first match):
  const cdnNum  = P.find((v) => /^cdn\d+$/.test(v));
  const tnmr    = P.find((v) => v === "tnmr");
  const hls2    = P.find((v) => v === "hls2");
  const d02     = P.find((v) => v === "02");
  const d03338  = P.find((v) => v === "03338");
  const vidDir  = P.find((v) => v.endsWith("_h"));
  const master  = P.find((v) => v === "master");
  const m3u8    = P.find((v) => v === "m3u8");
  const sig      = P.find((v) => /^[a-fA-F0-9]{32}$/.test(v));
  const ts        = P.find((v) => /^\d{10}$/.test(v));
  const fid       = P.find((v) => /^\d{8}$/.test(v));
  const eExp      = P.find((v) => v === "28800");

  const host = `https://${cdnNum}.cdn-${tnmr}.org`;
  const path = `/${hls2}/${d02}/${d03338}/${vidDir}/${master}.${m3u8}`;
  const url = `${host}${path}?t=${sig}&s=${ts}&e=${eExp}&f=${fid}&i=0.3&sp=0`;
  console.log("m3u8:", url);

  const r = await fetch(url, { headers: { "User-Agent": UA, "Referer": `https://luluvdo.com/e/${token}` } });
  const body = await r.text();
  console.log("fetch status:", r.status, r.headers.get("content-type"));
  console.log("body head:", body.slice(0, 200).replace(/\n/g, " | "));
})();

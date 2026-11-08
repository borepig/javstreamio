// Determine exactly what makes the Lulustream m3u8 return 200 (headers / cf_clearance).
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36";
const TOKEN = "q984z88m3llp";

async function buildUrl() {
  const res = await fetch(`https://luluvdo.com/e/${TOKEN}`, { headers: { "User-Agent": UA } });
  const t = await res.text();
  const m = t.match(/'([^']*hls2[^']*)'\s*\.split\('\|'\)/);
  const P = m[1].split("|");
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
  return `https://${cdnNum}.cdn-${tnmr}.org/${hls2}/${d02}/${d03338}/${vidDir}/${master}.${m3u8}?t=${sig}&s=${ts}&e=${eExp}&f=${fid}&i=0.3&sp=0`;
}

const url = await buildUrl();
const base = { "User-Agent": UA, "Referer": `https://luluvdo.com/e/${TOKEN}`, "Origin": "https://luluvdo.com", "Accept": "*/*" };

// 1) bare (no cookie)
let r = await fetch(url, { headers: base });
console.log("[no cookie]", r.status);

// 2) with a cf_clearance placeholder (user would supply a real one)
const clearance = process.env.CF_CLEARANCE || "";
if (clearance) {
  const h2 = { ...base, "Cookie": `cf_clearance=${clearance}` };
  r = await fetch(url, { headers: h2 });
  console.log("[cf_clearance]", r.status);
} else {
  console.log("[cf_clearance] (env not set, skipped)");
}
// 3) with Accept video/*
const h3 = { ...base, "Accept": "application/vnd.apple.mpegurl" };
r = await fetch(url, { headers: h3 });
console.log("[Accept mpegurl]", r.status);

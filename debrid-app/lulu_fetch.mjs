// Reconstruct the Lulustream m3u8 URL from the pipe-delimited parts and
// fetch it FROM THE SAME PROCESS (same IP/session) to test session-binding.
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36";
const LULU = "https://luluvdo.com/e/q984z88m3llp";

(async () => {
  const res = await fetch(LULU, { headers: { "User-Agent": UA, Referer: "https://www.underhentai.net/" } });
  const t = await res.text();
  const m = t.match(/'([^']*hls2[^']*)'\.split\('\|'\)/);
  if (!m) { console.log("no pipe string"); return; }
  const P = m[1].split("|");
  // Correct indices (verified against the key|value stream):
  const host = `https://${P[288]}.cdn-${P[286]}.org`;   // cdn1015.cdn-tnmr.org
  const path = `/${P[285]}/${P[284]}/${P[283]}/${P[282]}/${P[281]}.${P[280]}`;
  const tSig = P[279];      // signature
  const sTs  = P[127];      // timestamp
  const eExp = P[278];      // 28800
  const fId  = P[128];      // file id
  const iVal = "0.3";
  const spVal = "0";
  const url = `${host}${path}?t=${tSig}&s=${sTs}&e=${eExp}&f=${fId}&i=${iVal}&sp=${spVal}`;
  console.log("Reconstructed m3u8:", url);

  const r = await fetch(url, {
    headers: { "User-Agent": UA, "Referer": LULU },
  });
  const body = await r.text();
  console.log("Same-process fetch status:", r.status, r.headers.get("content-type"));
  console.log("Body head:", body.slice(0, 250).replace(/\n/g, " | "));
})();

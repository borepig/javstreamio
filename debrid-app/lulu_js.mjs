// Find the JS that builds the Lulustream m3u8 URL from the parts array.
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36";
(async () => {
  const res = await fetch("https://luluvdo.com/e/q984z88m3llp", { headers: { "User-Agent": UA } });
  const t = await res.text();
  // The parts array is defined as  var X = '...'.split('|')  ; find its variable name.
  const defM = t.match(/var\s+(\w+)\s*=\s*\'[^\']*hls2[^\']*\'\s*\.split\('\|'\)/);
  const varName = defM ? defM[1] : "PARTS";
  console.log("parts variable:", varName);
  // Find where the m3u8 URL is built (look for 'master' + '.m3u8' concatenation or 'cdn' usage)
  const idxs = [];
  const re = new RegExp(varName + "\\[(\\d+)\\]", "g");
  let m2;
  while ((m2 = re.exec(t)) !== null) {
    idxs.push(m2[1]);
  }
  console.log("indices referenced:", [...new Set(idxs)].sort((a, b) => a - b).join(", "));
  // Dump the code region that references the parts array around 'master' / 'm3u8'
  const k = t.indexOf("master");
  if (k >= 0) {
    console.log("\n=== code region around 'master' ===");
    console.log(t.slice(Math.max(0, k - 300), k + 300));
  }
})();

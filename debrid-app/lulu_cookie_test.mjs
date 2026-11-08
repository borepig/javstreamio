// Verify LULU m3u8 playback with a cf_clearance cookie (LULU_COOKIE).
// Run: LULU_COOKIE="cf_clearance=..." node lulu_cookie_test.mjs <token> [<token> ...]
// Uses the production proxyM3u8 path (buildLuluM3u8 + getLuluHeaders), which
// sends the LULU_COOKIE as the Cookie header to the CDN.
import { proxyM3u8 } from "./lib/lulustream.js";

const tokens = process.argv.slice(2);
if (!tokens.length) {
  console.log('usage: LULU_COOKIE="cf_clearance=..." node lulu_cookie_test.mjs <token>...');
  process.exit(1);
}
let ok = 0, fail = 0;
for (const tok of tokens) {
  try {
    await proxyM3u8(tok);
    console.log(tok, "PLAYABLE (m3u8 200)");
    ok++;
  } catch (e) {
    console.log(tok, "FAILED:", e.message);
    fail++;
  }
}
console.log("== playable:", ok, "failed:", fail);

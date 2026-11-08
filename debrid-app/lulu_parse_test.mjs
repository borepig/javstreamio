import { buildLuluM3u8 } from "./lib/lulustream.js";

const tokens = process.argv.slice(2);
for (const token of tokens) {
  try {
    const { url } = await buildLuluM3u8(token);
    console.log(token, "->", url);
  } catch (e) {
    console.log(token, "ERROR:", e.message);
  }
}

import jpeg from "jpeg-js";

// DVD jackets (pl / fourhoi cover-n / MGStage pb_e) are landscape scans:
// the back cover is on the left and the portrait front cover is the right half.
// The gray "NOW PRINTING" placeholder is a small portrait JPEG (~20 KB).

export function jpegSize(buf) {
  if (!buf || buf.length < 10 || buf[0] !== 0xff || buf[1] !== 0xd8) return null;
  let i = 2;
  while (i < buf.length - 9) {
    if (buf[i] !== 0xff) {
      i += 1;
      continue;
    }
    const marker = buf[i + 1];
    if (marker >= 0xc0 && marker <= 0xc3) {
      return {
        height: buf.readUInt16BE(i + 5),
        width: buf.readUInt16BE(i + 7),
      };
    }
    if (marker === 0xd8 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd9)) {
      i += 2;
      continue;
    }
    const length = buf.readUInt16BE(i + 2);
    if (length < 2) return null;
    i += 2 + length;
  }
  return null;
}

// Dead sehuatang image hosts answer 200 with this QR card (1024xp.com)
// instead of the forum cover. Same bytes on every host.
export function isForumPlaceholder(byteLength, width, height) {
  return byteLength === 47630 && width === 291 && height === 324;
}

export function isPrintingStub(byteLength, width, height) {
  if (!(byteLength > 0 && byteLength < 40000 && height >= width)) return false;
  // Gray NOW PRINTING cards only. A real shop thumbnail (about 100x140)
  // is small too, and must not be discarded.
  const tinyCard = width >= 140 && width <= 170 && height >= 180 && height <= 220 && byteLength < 8000;
  const sheet = width >= 540 && width <= 640 && height >= 740 && height <= 860 && byteLength < 25000;
  return tinyCard || sheet;
}

// Forum preview sheets (about 1900x1200) are screenshot grids, not DVD jackets.
export function isScreenshotGrid(width, height) {
  return width >= 1400 || height >= 900;
}

export function cropJpegRightHalf(buf) {
  const dim = jpegSize(buf);
  if (!dim || dim.width <= dim.height) return null;
  let raw;
  try {
    raw = jpeg.decode(buf, { useTArray: true, formatAsRGBA: true });
  } catch {
    return null;
  }
  let cw = Math.floor(raw.width / 2);
  if (cw % 2) cw -= 1;
  if (cw < 2) return null;
  const x = raw.width - cw;
  const h = raw.height % 2 ? raw.height - 1 : raw.height;
  const pixels = Buffer.from(raw.data);
  const out = Buffer.alloc(cw * h * 4);
  for (let y = 0; y < h; y++) {
    const src = (y * raw.width + x) * 4;
    pixels.copy(out, y * cw * 4, src, src + cw * 4);
  }
  const encoded = jpeg.encode({ data: out, width: cw, height: h }, 90);
  return Buffer.from(encoded.data);
}

export async function resolveJacketImage(sources, fetchImage, { fanart = false } = {}) {
  let portrait = null;
  for (const url of sources || []) {
    let buf;
    try {
      buf = await fetchImage(url);
    } catch {
      continue;
    }
    if (!buf) continue;
    if (!Buffer.isBuffer(buf)) buf = Buffer.from(buf);
    const dim = jpegSize(buf);
    if (!dim) continue;
    if (isScreenshotGrid(dim.width, dim.height)) continue;
    if (isPrintingStub(buf.length, dim.width, dim.height)) continue;
    if (dim.width > dim.height) {
      if (fanart) return buf;
      return cropJpegRightHalf(buf) || buf;
    }
    if (!portrait) portrait = buf;
  }
  return portrait;
}

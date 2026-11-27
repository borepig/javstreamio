import { test } from "node:test";
import assert from "node:assert/strict";
import jpeg from "jpeg-js";
import { cropJpegRightHalf, isPrintingStub, isScreenshotGrid, resolveJacketImage } from "./jacket-poster.js";

function solidJpeg(width, height, rgba) {
  const data = Buffer.alloc(width * height * 4);
  for (let i = 0; i < width * height; i++) {
    data[i * 4] = rgba[0];
    data[i * 4 + 1] = rgba[1];
    data[i * 4 + 2] = rgba[2];
    data[i * 4 + 3] = rgba[3];
  }
  return Buffer.from(jpeg.encode({ data, width, height }, 100).data);
}

function splitJpeg(width, height) {
  const data = Buffer.alloc(width * height * 4);
  const mid = Math.floor(width / 2);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const o = (y * width + x) * 4;
      if (x < mid) {
        data[o] = 220;
        data[o + 3] = 255;
      } else {
        data[o + 2] = 220;
        data[o + 3] = 255;
      }
    }
  }
  return Buffer.from(jpeg.encode({ data, width, height }, 100).data);
}

test("cropJpegRightHalf keeps the right half of a landscape jacket", () => {
  const cropped = cropJpegRightHalf(splitJpeg(8, 4));
  const out = jpeg.decode(cropped, { useTArray: true, formatAsRGBA: true });
  assert.equal(out.width, 4);
  assert.equal(out.height, 4);
  assert.ok(out.data[2] > 150, "right half should stay blue");
  assert.ok(out.data[0] < 40, "left (red) half should be gone");
});

test("cropJpegRightHalf leaves a portrait poster untouched", () => {
  const portrait = solidJpeg(4, 6, [10, 20, 30, 255]);
  assert.equal(cropJpegRightHalf(portrait), null);
});

test("isScreenshotGrid rejects forum preview sheets and keeps DVD jackets", () => {
  assert.equal(isScreenshotGrid(1914, 1188), true);
  assert.equal(isScreenshotGrid(800, 538), false);
});

test("isPrintingStub matches the small gray NOW PRINTING jpeg", () => {
  assert.equal(isPrintingStub(19378, 590, 800), true);
  assert.equal(isPrintingStub(3424, 147, 200), true);
  assert.equal(isPrintingStub(121397, 800, 538), false);
  assert.equal(isPrintingStub(7355, 100, 140), false);
});

test("resolveJacketImage skips the printing stub and crops the landscape fanart", async () => {
  const stub = solidJpeg(4, 6, [200, 200, 200, 255]);
  const jacket = splitJpeg(8, 4);
  const calls = [];
  const body = await resolveJacketImage(
    ["https://example.test/stub.jpg", "https://example.test/jacket.jpg"],
    async (url) => {
      calls.push(url);
      return url.endsWith("stub.jpg") ? stub : jacket;
    },
    { fanart: false },
  );
  const out = jpeg.decode(body, { useTArray: true, formatAsRGBA: true });
  assert.deepEqual(calls, [
    "https://example.test/stub.jpg",
    "https://example.test/jacket.jpg",
  ]);
  assert.equal(out.width, 4);
  assert.ok(out.data[2] > 150);
});

test("resolveJacketImage returns the full landscape image for fanart", async () => {
  const jacket = splitJpeg(8, 4);
  const body = await resolveJacketImage(
    ["https://example.test/jacket.jpg"],
    async () => jacket,
    { fanart: true },
  );
  assert.equal(body, jacket);
});

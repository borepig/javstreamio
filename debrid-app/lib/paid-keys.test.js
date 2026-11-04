import { test } from "node:test";
import assert from "node:assert/strict";
import { isValidPaidKey, PAID_KEYS } from "./paid-keys.js";

test("unknown or empty key is not paid", () => {
  assert.equal(isValidPaidKey(""), false);
  assert.equal(isValidPaidKey("nope"), false);
  assert.equal(isValidPaidKey(undefined), false);
});

test("lifetime key is valid", () => {
  PAID_KEYS.push({ key: "test-lifetime", plan: "lifetime" });
  try {
    assert.equal(isValidPaidKey("test-lifetime"), true);
  } finally {
    PAID_KEYS.pop();
  }
});

test("unexpired monthly key is valid", () => {
  PAID_KEYS.push({
    key: "test-monthly",
    plan: "monthly",
    issuedAt: "2026-11-04",
    expiresAt: "2026-12-04",
  });
  try {
    assert.equal(isValidPaidKey("test-monthly"), true);
  } finally {
    PAID_KEYS.pop();
  }
});

test("expired monthly key is not valid", () => {
  PAID_KEYS.push({ key: "test-expired", plan: "monthly", issuedAt: "2020-01-01", expiresAt: "2020-02-01" });
  try {
    assert.equal(isValidPaidKey("test-expired"), false);
  } finally {
    PAID_KEYS.pop();
  }
});

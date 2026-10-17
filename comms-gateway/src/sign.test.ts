import { describe, expect, it } from "vitest";
import { signBody, signHeaders } from "./sign.js";

// Pinned against backend/app/security.py: sign_event(secret, ts, raw) =
// hex(HMAC_SHA256(secret, "<ts>." + raw)). Value generated with Python's hmac.
const SECRET = "test-secret";
const TS = 1_760_000_000_000;
const BODY = '{"from":"+13105550101","body":"UNLOCK 4821"}';
const EXPECTED =
  "d5be81f2da7609d6ff301334cb47df73d7a100ba72e2776a6e0cf2e74cf6b67a";

describe("signBody", () => {
  it("matches the backend's HMAC_SHA256(ts + '.' + raw_body) scheme", () => {
    expect(signBody(SECRET, TS, BODY)).toBe(EXPECTED);
  });
});

describe("signHeaders", () => {
  it("emits a current X-Timestamp and the matching X-Signature", () => {
    const before = Date.now();
    const h = signHeaders(SECRET, BODY);
    const ts = Number(h["x-timestamp"]);
    expect(ts).toBeGreaterThanOrEqual(before);
    expect(ts).toBeLessThanOrEqual(Date.now());
    expect(h["x-signature"]).toBe(signBody(SECRET, ts, BODY));
  });
});

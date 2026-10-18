import { afterEach, describe, expect, it, vi } from "vitest";
import type { Config } from "./config.js";
import { signBody, signHeaders } from "./sign.js";
import { forwardInbound } from "./server.js";

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

describe("forwardInbound", () => {
  afterEach(() => vi.unstubAllGlobals());

  function cfg(sharedSecret: string): Config {
    return {
      port: 8787,
      stubPhoton: true,
      backendUrl: "http://backend/",
      sharedSecret,
      photon: { token: "", projectId: "", agentHandle: "" },
    };
  }

  function captureFetch(): Array<{ url: string; init: RequestInit }> {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    vi.stubGlobal("fetch", async (url: string | URL, init: RequestInit) => {
      calls.push({ url: String(url), init });
      return new Response("{}", { status: 200 });
    });
    return calls;
  }

  it("sends X-Signature/X-Timestamp when SHARED_SECRET is set", async () => {
    const calls = captureFetch();
    await forwardInbound(cfg("s3cr3t"), { sender: "+1", text: "hi" });
    const headers = calls[0]!.init.headers as Record<string, string>;
    expect(headers["x-signature"]).toMatch(/^[0-9a-f]{64}$/);
    expect(Number(headers["x-timestamp"])).toBeGreaterThan(0);
    expect(calls[0]!.url).toBe("http://backend/comms/inbound");
  });

  it("sends unsigned when the secret is empty (stub-mode path)", async () => {
    const calls = captureFetch();
    await forwardInbound(cfg(""), { sender: "+1", text: "hi" });
    const headers = calls[0]!.init.headers as Record<string, string>;
    expect(headers["x-signature"]).toBeUndefined();
    expect(headers["x-timestamp"]).toBeUndefined();
  });
});

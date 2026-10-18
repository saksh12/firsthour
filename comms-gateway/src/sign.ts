/**
 * Signing for gateway -> backend calls (IMPLEMENTATION.md section 6.6).
 *
 * The backend checks X-Signature = hex(HMAC_SHA256(SHARED_SECRET, ts + "." + raw_body))
 * with the timestamp carried in X-Timestamp (epoch ms) -- the same scheme the Pi
 * uses for POST /edge/event in backend/app/security.py. Keep the formats identical.
 */

import { createHmac } from "node:crypto";

export function signBody(secret: string, ts: number, rawBody: string): string {
  return createHmac("sha256", secret).update(`${ts}.`).update(rawBody).digest("hex");
}

/** Headers for a signed POST to the backend. */
export function signHeaders(secret: string, rawBody: string): Record<string, string> {
  const ts = Date.now();
  return {
    "x-signature": signBody(secret, ts, rawBody),
    "x-timestamp": String(ts),
  };
}

/**
 * HTTP surface of the gateway (Role C).
 *
 *   GET  /health   -> { ok, stub }
 *   POST /send      { to, body, spaceId?, senderId?, parentMessageId? }
 *                   -> sends via Photon (or stub outbox)
 *   POST /inbound    a Photon received-message (webhook), or { from, body }
 *                   -> forwards to the backend's POST /comms/inbound
 *
 * In the real path a background poller also pulls received iMessages and
 * forwards caregiver replies, so no public webhook is strictly required.
 */

import { createServer as createHttpServer, type IncomingMessage, type ServerResponse } from "node:http";
import type { Config } from "./config.js";
import {
  isCaregiverReply,
  toBackendInbound,
  type InboundForward,
  type ReceivedMessage,
} from "./messages.js";
import type { PhotonGateway } from "./photon.js";
import { signHeaders } from "./sign.js";

async function readJson(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const c of req) chunks.push(c as Buffer);
  const raw = Buffer.concat(chunks).toString("utf8");
  return raw ? JSON.parse(raw) : {};
}

function send(res: ServerResponse, status: number, payload: unknown): void {
  const body = JSON.stringify(payload);
  res.writeHead(status, { "content-type": "application/json" });
  res.end(body);
}

/** Forward one caregiver reply to the backend, signed (section 6.6).
 *  Returns the backend's JSON. */
export async function forwardInbound(
  backendUrl: string,
  sharedSecret: string,
  payload: InboundForward | { from: string; body: string },
): Promise<unknown> {
  const body = JSON.stringify(payload);
  const resp = await fetch(`${backendUrl.replace(/\/$/, "")}/comms/inbound`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      // Unsigned when the secret is unset: the backend then only accepts the
      // forward while its STUB_PHOTON=1 (the simulator path); index.ts warns.
      ...(sharedSecret ? signHeaders(sharedSecret, body) : {}),
    },
    body,
  });
  if (!resp.ok) throw new Error(`backend /comms/inbound -> ${resp.status}`);
  return resp.json().catch(() => ({}));
}

export function createServer(cfg: Config, gateway: PhotonGateway) {
  return createHttpServer(async (req, res) => {
    try {
      const url = req.url ?? "/";
      if (req.method === "GET" && url === "/health") {
        return send(res, 200, { ok: true, stub: cfg.stubPhoton });
      }

      if (req.method === "POST" && url === "/send") {
        const b = (await readJson(req)) as {
          to?: string;
          body?: string;
          spaceId?: string;
          senderId?: string;
          parentMessageId?: string;
        };
        if (!b.to || !b.body) return send(res, 400, { error: "to and body are required" });
        const result = await gateway.send({
          to: b.to,
          body: b.body,
          spaceId: b.spaceId,
          senderId: b.senderId,
          parentMessageId: b.parentMessageId,
        });
        return send(res, 200, result);
      }

      if (req.method === "POST" && url === "/inbound") {
        const b = (await readJson(req)) as Partial<ReceivedMessage> & {
          from?: string;
          body?: string;
        };
        // Either a raw Photon received-message, or a simple {from, body}.
        let payload: InboundForward | { from: string; body: string };
        if (b.id && b.sentBy && b.space) {
          payload = toBackendInbound(b as ReceivedMessage);
        } else if (b.from && typeof b.body === "string") {
          payload = { from: b.from, body: b.body };
        } else {
          return send(res, 400, { error: "expected a Photon message or { from, body }" });
        }
        const backendReply = await forwardInbound(cfg.backendUrl, cfg.sharedSecret, payload);
        return send(res, 200, { forwarded: true, backend: backendReply });
      }

      return send(res, 404, { error: "not found" });
    } catch (err) {
      return send(res, 500, { error: err instanceof Error ? err.message : "gateway error" });
    }
  });
}

/**
 * Background inbound poller (real path only). Polls Photon for received
 * iMessages and forwards caregiver replies to the backend. Returns a stop fn.
 */
export function startInboundPoller(
  cfg: Config,
  gateway: PhotonGateway,
  intervalMs = 2000,
): () => void {
  let stopped = false;
  let since = new Date(Date.now() - 5000).toISOString();
  const seen = new Set<string>();

  const tick = async (): Promise<void> => {
    if (stopped) return;
    try {
      const msgs = await gateway.listReceivedSince(since);
      for (const m of msgs) {
        if (seen.has(m.id)) continue;
        seen.add(m.id);
        if (isCaregiverReply(m, cfg.photon.agentHandle)) {
          await forwardInbound(cfg.backendUrl, cfg.sharedSecret, toBackendInbound(m));
        }
        if (m.createdAt > since) since = m.createdAt;
      }
    } catch (err) {
      console.error("[poller]", err instanceof Error ? err.message : err);
    } finally {
      if (!stopped) setTimeout(tick, intervalMs);
    }
  };

  void tick();
  return () => {
    stopped = true;
  };
}

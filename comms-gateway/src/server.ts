/**
 * HTTP surface of the gateway (Role C). Listens on localhost only: the backend
 * on the same laptop is its only client.
 *
 *   GET  /health   -> { ok, stub }
 *   POST /send      { to, body } -> sends via Photon (or the stub outbox)
 *
 * Inbound: a background poller pulls received iMessages and forwards caregiver
 * replies to the backend's POST /comms/inbound, so no public webhook is needed.
 */

import { createServer as createHttpServer, type IncomingMessage, type ServerResponse } from "node:http";
import type { Config } from "./config.js";
import { conversationOf, isCaregiverReply, toBackendInbound, type InboundForward } from "./messages.js";
import { NoConversationError, type PhotonGateway } from "./photon.js";

async function readJson(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const c of req) chunks.push(c as Buffer);
  const raw = Buffer.concat(chunks).toString("utf8");
  return raw ? JSON.parse(raw) : {};
}

function send(res: ServerResponse, status: number, payload: unknown): void {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(payload));
}

/** Forward one caregiver reply to the backend. Returns the backend's JSON. */
export async function forwardInbound(cfg: Config, payload: InboundForward): Promise<unknown> {
  const resp = await fetch(`${cfg.backendUrl.replace(/\/$/, "")}/comms/inbound`, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${cfg.backendToken}` },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) throw new Error(`backend /comms/inbound -> ${resp.status}`);
  return resp.json().catch(() => ({}));
}

export function createServer(cfg: Config, gateway: PhotonGateway) {
  return createHttpServer(async (req, res) => {
    try {
      if (req.method === "GET" && req.url === "/health") {
        return send(res, 200, { ok: true, stub: cfg.stubPhoton });
      }
      if (req.method === "POST" && req.url === "/send") {
        const b = (await readJson(req)) as { to?: unknown; body?: unknown };
        if (typeof b.to !== "string" || typeof b.body !== "string" || !b.to || !b.body) {
          return send(res, 400, { error: "to and body are required strings" });
        }
        return send(res, 200, await gateway.send(b.to, b.body));
      }
      return send(res, 404, { error: "not found" });
    } catch (err) {
      const status = err instanceof NoConversationError ? 409 : 500;
      return send(res, status, { error: err instanceof Error ? err.message : "gateway error" });
    }
  });
}

/**
 * One poll of Photon for received iMessages, forwarding caregiver replies to the
 * backend. A message is marked seen, and `since` moves past it, only once its forward
 * succeeds: if the backend is down for a tick, the reply is retried on the next one.
 * Messages come oldest first, so the tick stops at the first failure and nothing
 * after it is skipped.
 */
export function createInboundPoll(
  cfg: Config,
  gateway: Pick<PhotonGateway, "listReceivedSince" | "remember">,
  forward: (cfg: Config, payload: InboundForward) => Promise<unknown> = forwardInbound,
): () => Promise<void> {
  // In memory: a gateway restart still drops replies in the gap. A durable cursor is the Photon issue.
  let since = new Date(Date.now() - 5000).toISOString();
  const seen = new Set<string>();

  return async () => {
    for (const m of await gateway.listReceivedSince(since)) {
      if (seen.has(m.id)) continue;
      if (isCaregiverReply(m, cfg.photon.agentHandle)) {
        gateway.remember(m.sentBy.handle, conversationOf(m));
        await forward(cfg, toBackendInbound(m));
      }
      seen.add(m.id);
      if (m.createdAt > since) since = m.createdAt;
    }
  };
}

/** Background inbound poller (real path only). Returns a stop fn. */
export function startInboundPoller(cfg: Config, gateway: PhotonGateway, intervalMs = 2000): () => void {
  let stopped = false;
  const poll = createInboundPoll(cfg, gateway);

  const tick = async (): Promise<void> => {
    if (stopped) return;
    try {
      await poll();
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

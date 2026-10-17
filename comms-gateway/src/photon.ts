/**
 * Photon client wrapper (Role C). The single place that talks to Photon.
 *
 * STUB_PHOTON=1 (default): no SDK, no creds, no network -- send() records to an
 * in-memory outbox and the poller is never started. The caregiver simulator
 * page covers inbound for the demo.
 * STUB_PHOTON=0: lazy-load @photon-ai/api and use the real Beta (2.0) API.
 *
 * Docs: https://photon.codes/docs/beta/get-started/imessage
 */

import type { Config } from "./config.js";
import { buildSendBody, type ReceivedMessage } from "./messages.js";

export interface SendResult {
  id: string;
  stubbed: boolean;
}

/** Minimal shape of the @photon-ai/api client we use, so typecheck does not
 *  require the optional SDK to be installed. */
interface PhotonLike {
  projects: {
    sendMessage(args: {
      path: { id: string };
      headers?: Record<string, string>;
      body: unknown;
    }): Promise<{ id?: string }>;
    listMessages(args: {
      path: { id: string };
      query: Record<string, unknown>;
    }): Promise<{ items: ReceivedMessage[]; nextPageToken?: string }>;
  };
}

const stubOutbox: Array<{ to: string; body: string }> = [];

export function outbox(): ReadonlyArray<{ to: string; body: string }> {
  return stubOutbox;
}

export class PhotonGateway {
  private client: PhotonLike | null = null;

  constructor(private readonly cfg: Config) {}

  private async getClient(): Promise<PhotonLike> {
    if (this.client) return this.client;
    let mod: { Photon: new (opts: unknown) => PhotonLike };
    try {
      mod = (await import("@photon-ai/api")) as unknown as typeof mod;
    } catch {
      throw new Error(
        "@photon-ai/api is not installed. Run `npm install` in comms-gateway " +
          "and set STUB_PHOTON=0 only once Photon creds are in .env.",
      );
    }
    this.client = new mod.Photon({
      headers: { Authorization: `Bearer ${this.cfg.photon.token}` },
    });
    return this.client;
  }

  /**
   * Send one message. In the real path this replies within an existing
   * conversation (spaceId + senderId from a prior inbound). A shared Photon
   * line CANNOT start a conversation, so an alert needs the caregiver to have
   * messaged the agent line first -- see README. TODO(verify) at the Photon
   * table whether a dedicated line lifts this for outbound-first alerts.
   */
  async send(input: {
    to: string;
    body: string;
    spaceId?: string;
    senderId?: string;
    parentMessageId?: string;
  }): Promise<SendResult> {
    if (this.cfg.stubPhoton) {
      stubOutbox.push({ to: input.to, body: input.body });
      return { id: `stub-${stubOutbox.length}`, stubbed: true };
    }
    if (!input.spaceId || !input.senderId) {
      throw new Error(
        "real send needs spaceId + senderId (reply within a known conversation)",
      );
    }
    const client = await this.getClient();
    const res = await client.projects.sendMessage({
      path: { id: this.cfg.photon.projectId },
      headers: input.parentMessageId
        ? { "idempotency-key": `alert-reply:${input.parentMessageId}` }
        : undefined,
      body: buildSendBody({
        spaceId: input.spaceId,
        senderId: input.senderId,
        parentMessageId: input.parentMessageId,
        text: input.body,
      }),
    });
    return { id: res.id ?? "accepted", stubbed: false };
  }

  /** One page of received iMessages since `createdSince` (ISO). Real path only. */
  async listReceivedSince(createdSince: string): Promise<ReceivedMessage[]> {
    const client = await this.getClient();
    const out: ReceivedMessage[] = [];
    let pageToken: string | undefined;
    do {
      const page = await client.projects.listMessages({
        path: { id: this.cfg.photon.projectId },
        query: {
          createdSince,
          direction: "received",
          order: "asc",
          pageSize: 100,
          platform: "imessage",
          ...(pageToken ? { pageToken } : {}),
        },
      });
      out.push(...page.items);
      pageToken = page.nextPageToken;
    } while (pageToken);
    return out;
  }
}

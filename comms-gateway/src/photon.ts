/**
 * Photon client wrapper (Role C). The single place that talks to Photon.
 *
 * STUB_PHOTON=1 (default): no creds, no network -- send() records to an
 * in-memory outbox and the poller is never started. The caregiver simulator
 * page covers inbound for the demo.
 * STUB_PHOTON=0: the real Beta (2.0) API via @photon-ai/api (pinned; typed calls).
 *
 * Docs: https://photon.codes/docs/beta/get-started/imessage
 */

import { randomUUID } from "node:crypto";
import { Photon } from "@photon-ai/api";
import type { Config } from "./config.js";
import { buildSendBody, type Conversation, type ReceivedMessage } from "./messages.js";
import { GatewayState } from "./state.js";

export interface SendResult {
  id: string;
  stubbed: boolean;
}

export class NoConversationError extends Error {}

export class PhotonGateway {
  private client: Photon | null = null;
  readonly stubOutbox: Array<{ to: string; body: string }> = [];
  // A shared Photon line can't start a conversation (SHARED_LINE_INBOUND_REQUIRED), so
  // we reply in the caregiver's last conversation with the agent line. Kept on disk.
  constructor(
    private readonly cfg: Config,
    readonly state: GatewayState = new GatewayState(""),
  ) {}

  private photon(): Photon {
    this.client ??= new Photon({ headers: { Authorization: `Bearer ${this.cfg.photon.token}` } });
    return this.client;
  }

  remember(handle: string, conversation: Conversation): void {
    this.state.remember(handle, conversation);
  }

  async send(to: string, body: string): Promise<SendResult> {
    if (this.cfg.stubPhoton) {
      this.stubOutbox.push({ to, body });
      return { id: `stub-${this.stubOutbox.length}`, stubbed: true };
    }
    const conv = this.state.conversation(to);
    if (!conv) {
      // TODO(verify) at the Photon table: does a dedicated line let us text first?
      throw new NoConversationError(
        `no conversation with ${to} yet: the caregiver must text the agent line once first`,
      );
    }
    const res = await this.photon().projects.sendMessage({
      path: { id: this.cfg.photon.projectId },
      headers: { "idempotency-key": randomUUID() },
      body: buildSendBody({ ...conv, text: body }),
    });
    return { id: typeof res.id === "string" ? res.id : "accepted", stubbed: false };
  }

  /** Every received iMessage since `createdSince` (ISO), oldest first. Real path only. */
  async listReceivedSince(createdSince: string): Promise<ReceivedMessage[]> {
    const out: ReceivedMessage[] = [];
    let pageToken: string | undefined;
    do {
      const page = await this.photon().projects.listMessages({
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
      // The SDK's Message union includes an UNKNOWN variant; isCaregiverReply checks the fields we read.
      out.push(...(page.items as unknown as ReceivedMessage[]));
      pageToken = page.nextPageToken;
    } while (pageToken);
    return out;
  }
}

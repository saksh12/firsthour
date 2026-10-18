/**
 * Durable gateway state (issue #23): the inbound cursor, the ids already forwarded
 * at that cursor, and each caregiver's conversation. Survives a restart, so a reply
 * that arrives while the gateway is down is forwarded when it comes back, and alerts
 * can still reach a caregiver who texted the shared line before the restart.
 *
 * The cursor moves only after the backend accepted the message (forwardInbound).
 * Written atomically (tmp + rename). Holds phone numbers: keep it out of git.
 */

import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import type { Conversation } from "./messages.js";

interface Saved {
  since: string | null;
  seenAtSince: string[];
  conversations: Record<string, Conversation>;
}

export class GatewayState {
  private data: Saved = { since: null, seenAtSince: [], conversations: {} };

  /** path "" keeps everything in memory (tests, STUB_PHOTON). */
  constructor(private readonly path: string) {
    if (!path) return;
    try {
      const raw = JSON.parse(readFileSync(path, "utf8")) as Partial<Saved>;
      this.data = {
        since: typeof raw.since === "string" ? raw.since : null,
        seenAtSince: Array.isArray(raw.seenAtSince) ? raw.seenAtSince.filter((x) => typeof x === "string") : [],
        conversations: raw.conversations && typeof raw.conversations === "object" ? raw.conversations : {},
      };
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code !== "ENOENT") {
        console.error(`[state] ${path} unreadable, starting fresh:`, err instanceof Error ? err.message : err);
      }
    }
  }

  get since(): string | null {
    return this.data.since;
  }

  /** Forwarded already: same createdAt as the cursor and in the seen list. */
  seen(id: string, createdAt: string): boolean {
    return createdAt < (this.data.since ?? "") || (createdAt === this.data.since && this.data.seenAtSince.includes(id));
  }

  /** Call only after the backend accepted message `id` (or it was skipped on purpose). */
  advance(id: string, createdAt: string): void {
    if (this.data.since === null || createdAt > this.data.since) {
      this.data.since = createdAt;
      this.data.seenAtSince = [id];
    } else if (createdAt === this.data.since && !this.data.seenAtSince.includes(id)) {
      this.data.seenAtSince.push(id);
    }
    this.save();
  }

  conversation(handle: string): Conversation | undefined {
    return this.data.conversations[handle];
  }

  remember(handle: string, conversation: Conversation): void {
    const prev = this.data.conversations[handle];
    if (prev && prev.spaceId === conversation.spaceId && prev.senderId === conversation.senderId) return;
    this.data.conversations[handle] = conversation;
    this.save();
  }

  private save(): void {
    if (!this.path) return;
    mkdirSync(dirname(this.path), { recursive: true });
    const tmp = `${this.path}.tmp`;
    writeFileSync(tmp, JSON.stringify(this.data), { mode: 0o600 });
    renameSync(tmp, this.path);
  }
}

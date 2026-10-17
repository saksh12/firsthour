/**
 * Pure message logic for the Photon gateway (Role C).
 *
 * No network, no SDK imports -- just mapping Photon message shapes to/from the
 * small payloads FirstHour's backend uses. Everything here is unit-tested.
 *
 * Shapes follow the Photon Beta (2.0) walkthrough:
 *   https://photon.codes/docs/beta/get-started/imessage
 * We type only the subset of fields we read, so the gateway does not depend on
 * the SDK's generated types.
 */

export interface UserSnapshot {
  platform: string;
  kind: "human" | "agent" | string;
  handle: string; // E.164 phone, or iMessage email
  id: string;
}

export interface SpaceSnapshot {
  platform: string;
  kind: "direct" | "group" | string;
  id: string;
}

export interface TextContent {
  type: "text";
  format: "plain" | "markdown" | string;
  text: string;
}

export interface GroupContent {
  type: "group";
  parts: Array<{ type: string; format?: string; text?: string }>;
}

export type MessageContent = TextContent | GroupContent | { type: string };

export interface ReceivedMessage {
  id: string;
  platform: string;
  status: string;
  createdAt: string;
  sentBy: UserSnapshot;
  receivedBy?: UserSnapshot;
  space: SpaceSnapshot;
  content: MessageContent;
}

/** What the gateway forwards to the backend's POST /comms/inbound. */
export interface InboundForward {
  from: string; // caregiver's phone/email (the sender's number -- Q3: yes)
  body: string; // the reply text
  platform: string;
  spaceId: string; // reuse to reply in the same conversation
  senderId: string; // the agent's user id, used as senderId when replying
  messageId: string; // reply's parentMessageId + idempotency anchor
}

/** Body for photon.projects.sendMessage(...). */
export interface SendBody {
  spaceId: string;
  senderId: string;
  parentMessageId?: string;
  content: TextContent;
}

export function extractPlainText(content: MessageContent): string | null {
  if (content.type === "text") {
    const t = content as TextContent;
    return t.format === "plain" ? t.text : null;
  }
  if (content.type === "group") {
    const parts = (content as GroupContent).parts.filter(
      (p) => p.type === "text" && p.format === "plain" && typeof p.text === "string",
    );
    return parts.length === 1 ? (parts[0]!.text as string) : null;
  }
  return null;
}

/**
 * A genuine inbound caregiver reply: a received plain-text iMessage addressed
 * to our agent line, sent by a human (not our own outbound echo).
 */
export function isCaregiverReply(msg: ReceivedMessage, agentHandle: string): boolean {
  if (msg.platform !== "imessage" || msg.status !== "received") return false;
  if (!msg.receivedBy) return false;
  if (msg.sentBy.kind !== "human") return false;
  if (msg.receivedBy.kind !== "agent" || msg.receivedBy.handle !== agentHandle) return false;
  if (msg.sentBy.id === msg.receivedBy.id) return false; // never answer ourselves
  if (extractPlainText(msg.content) === null) return false;
  return true;
}

export function toBackendInbound(msg: ReceivedMessage): InboundForward {
  const text = extractPlainText(msg.content);
  if (text === null) throw new Error("message has no plain text to forward");
  if (!msg.receivedBy) throw new Error("message has no receivedBy agent");
  return {
    from: msg.sentBy.handle,
    body: text,
    platform: msg.platform,
    spaceId: msg.space.id,
    senderId: msg.receivedBy.id,
    messageId: msg.id,
  };
}

export function buildSendBody(input: {
  spaceId: string;
  senderId: string;
  parentMessageId?: string;
  text: string;
}): SendBody {
  const body: SendBody = {
    spaceId: input.spaceId,
    senderId: input.senderId,
    content: { type: "text", format: "plain", text: input.text },
  };
  if (input.parentMessageId) body.parentMessageId = input.parentMessageId;
  return body;
}

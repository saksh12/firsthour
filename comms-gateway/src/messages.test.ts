import { describe, expect, it } from "vitest";
import {
  buildSendBody,
  conversationOf,
  extractPlainText,
  isCaregiverReply,
  toBackendInbound,
  type ReceivedMessage,
} from "./messages.js";

const AGENT = "+14155550188";
const CAREGIVER = "+13105550101";

function received(over: Partial<ReceivedMessage> = {}): ReceivedMessage {
  return {
    id: "msg_1",
    platform: "imessage",
    status: "received",
    createdAt: "2026-10-17T12:00:00.000Z",
    sentBy: { platform: "imessage", kind: "human", handle: CAREGIVER, id: "u_human" },
    receivedBy: { platform: "imessage", kind: "agent", handle: AGENT, id: "u_agent" },
    space: { platform: "imessage", kind: "direct", id: "space_1" },
    content: { type: "text", format: "plain", text: "UNLOCK 4821" },
    ...over,
  };
}

describe("extractPlainText", () => {
  it("reads a plain text message", () => {
    expect(extractPlainText({ type: "text", format: "plain", text: "hi" })).toBe("hi");
  });
  it("ignores non-plain text", () => {
    expect(extractPlainText({ type: "text", format: "markdown", text: "*hi*" })).toBeNull();
  });
  it("reads a single plain part from a group", () => {
    expect(
      extractPlainText({ type: "group", parts: [{ type: "text", format: "plain", text: "hi" }] }),
    ).toBe("hi");
  });
  it("returns null for an ambiguous multi-part group", () => {
    expect(
      extractPlainText({
        type: "group",
        parts: [
          { type: "text", format: "plain", text: "a" },
          { type: "text", format: "plain", text: "b" },
        ],
      }),
    ).toBeNull();
  });
});

describe("isCaregiverReply", () => {
  it("accepts a received plain-text human->agent iMessage", () => {
    expect(isCaregiverReply(received(), AGENT)).toBe(true);
  });
  it("rejects a message to a different agent handle", () => {
    expect(isCaregiverReply(received(), "+19999999999")).toBe(false);
  });
  it("rejects our own outbound (agent sender)", () => {
    const m = received({
      sentBy: { platform: "imessage", kind: "agent", handle: AGENT, id: "u_agent" },
    });
    expect(isCaregiverReply(m, AGENT)).toBe(false);
  });
  it("rejects non-received status", () => {
    expect(isCaregiverReply(received({ status: "sent" }), AGENT)).toBe(false);
  });
  it("rejects a non-text message", () => {
    expect(isCaregiverReply(received({ content: { type: "image" } }), AGENT)).toBe(false);
  });
});

describe("toBackendInbound", () => {
  it("maps to the backend's {sender, text}, incl. the sender's number", () => {
    expect(toBackendInbound(received())).toEqual({ sender: CAREGIVER, text: "UNLOCK 4821" });
  });
  it("throws when there is no plain text", () => {
    expect(() => toBackendInbound(received({ content: { type: "image" } }))).toThrow();
  });
});

describe("conversationOf", () => {
  it("replies in the same space as our agent user", () => {
    expect(conversationOf(received())).toEqual({ spaceId: "space_1", senderId: "u_agent" });
  });
});

describe("buildSendBody", () => {
  it("builds a plain text send body", () => {
    expect(buildSendBody({ spaceId: "s", senderId: "a", text: "hi" })).toEqual({
      spaceId: "s",
      senderId: "a",
      content: { type: "text", format: "plain", text: "hi" },
    });
  });
});

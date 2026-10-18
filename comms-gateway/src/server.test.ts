import { describe, expect, it } from "vitest";
import type { Config } from "./config.js";
import type { InboundForward, ReceivedMessage } from "./messages.js";
import { createInboundPoll } from "./server.js";

const AGENT = "+14155550188";
const CAREGIVER = "+13105550101";

const cfg: Config = {
  port: 0,
  stubPhoton: false,
  backendUrl: "http://backend",
  backendToken: "secret",
  photon: { token: "t", projectId: "p", agentHandle: AGENT },
};

function reply(id: string, text: string, createdAt: string): ReceivedMessage {
  return {
    id,
    platform: "imessage",
    status: "received",
    createdAt,
    sentBy: { platform: "imessage", kind: "human", handle: CAREGIVER, id: "u_human" },
    receivedBy: { platform: "imessage", kind: "agent", handle: AGENT, id: "u_agent" },
    space: { platform: "imessage", kind: "direct", id: "space_1" },
    content: { type: "text", format: "plain", text },
  };
}

describe("inbound poll", () => {
  it("retries a reply whose forward failed, and doesn't skip past it", async () => {
    const at = (ms: number) => new Date(Date.now() + ms).toISOString();
    const inbox = [reply("m1", "UNLOCK 4821", at(1000)), reply("m2", "on my way", at(2000))];
    const gateway = {
      listReceivedSince: async (since: string) => inbox.filter((m) => m.createdAt >= since),
      remember: () => {},
    };
    const forwarded: string[] = [];
    let fail = true;
    const forward = async (_cfg: Config, p: InboundForward) => {
      if (fail) {
        fail = false;
        throw new Error("backend /comms/inbound -> 502");
      }
      forwarded.push(p.text);
      return {};
    };
    const poll = createInboundPoll(cfg, gateway, forward);

    await expect(poll()).rejects.toThrow("502"); // backend down for one tick
    expect(forwarded).toEqual([]);
    await poll(); // next tick: both replies arrive, in order
    expect(forwarded).toEqual(["UNLOCK 4821", "on my way"]);
    await poll(); // and only once
    expect(forwarded).toEqual(["UNLOCK 4821", "on my way"]);
  });
});

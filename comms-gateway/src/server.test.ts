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
  it("forwards a reply that landed on a shared line other than PHOTON_AGENT_HANDLE", async () => {
    const m = reply("m1", "on my way", new Date(Date.now() + 1000).toISOString());
    m.receivedBy = { platform: "imessage", kind: "agent", handle: "+16282634622", id: "u_agent_2" };
    const remembered: Array<[string, unknown]> = [];
    const gateway = {
      listReceivedSince: async () => [m],
      remember: (h: string, c: unknown) => void remembered.push([h, c]),
    };
    const forwarded: InboundForward[] = [];
    await createInboundPoll(cfg, gateway, async (_c, p) => void forwarded.push(p))();
    expect(forwarded.map((p) => p.sender)).toEqual([CAREGIVER]);
    // and the reply goes back on that same line, not the configured one
    expect(remembered).toEqual([[CAREGIVER, { spaceId: "space_1", senderId: "u_agent_2" }]]);
  });

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

describe("durable inbound cursor", () => {
  it("a restarted gateway forwards the reply that arrived while it was down, once", async () => {
    const { mkdtempSync } = await import("node:fs");
    const { tmpdir } = await import("node:os");
    const { join } = await import("node:path");
    const { GatewayState } = await import("./state.js");
    const file = join(mkdtempSync(join(tmpdir(), "gw-")), "state.json");
    const t0 = Date.now();
    const at = (ms: number) => new Date(t0 + ms).toISOString();
    const inbox = [reply("m1", "on my way", at(1000))];
    const remembered: string[] = [];
    const gateway = {
      listReceivedSince: async (since: string) => inbox.filter((m) => m.createdAt >= since),
      remember: (h: string) => void remembered.push(h),
    };
    const forwarded: InboundForward[] = [];
    const forward = async (_c: Config, p: InboundForward) => void forwarded.push(p);

    await createInboundPoll(cfg, gateway, forward, new GatewayState(file))();
    expect(forwarded.map((p) => p.text)).toEqual(["on my way"]);
    expect(forwarded[0].messageId).toBe("m1");

    // Gateway is down; two replies land (one at the same createdAt as the cursor).
    inbox.push(reply("m2", "10 min", at(1000)), reply("m3", "UNLOCK 4821", at(60_000)));
    const restarted = createInboundPoll(cfg, gateway, forward, new GatewayState(file));
    await restarted();
    expect(forwarded.map((p) => p.text)).toEqual(["on my way", "10 min", "UNLOCK 4821"]);
    await restarted();
    expect(forwarded).toHaveLength(3);
  });

  it("keeps the caregiver's conversation across a restart", async () => {
    const { mkdtempSync } = await import("node:fs");
    const { tmpdir } = await import("node:os");
    const { join } = await import("node:path");
    const { GatewayState } = await import("./state.js");
    const file = join(mkdtempSync(join(tmpdir(), "gw-")), "state.json");
    new GatewayState(file).remember(CAREGIVER, { spaceId: "space_1", senderId: "u_agent" });
    expect(new GatewayState(file).conversation(CAREGIVER)).toEqual({ spaceId: "space_1", senderId: "u_agent" });
  });

  it("a failed forward leaves the saved cursor where it was", async () => {
    const { mkdtempSync } = await import("node:fs");
    const { tmpdir } = await import("node:os");
    const { join } = await import("node:path");
    const { GatewayState } = await import("./state.js");
    const file = join(mkdtempSync(join(tmpdir(), "gw-")), "state.json");
    const inbox = [reply("m1", "omw", new Date(Date.now() + 1000).toISOString())];
    const gateway = { listReceivedSince: async () => inbox, remember: () => {} };
    const failing = async () => {
      throw new Error("backend /comms/inbound -> 502");
    };
    await expect(createInboundPoll(cfg, gateway, failing, new GatewayState(file))()).rejects.toThrow("502");
    expect(new GatewayState(file).since).toBeNull();
  });
});

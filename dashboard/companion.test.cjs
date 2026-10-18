const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");

// Run the dashboard's companion code with fake network/audio boundaries, not a live mic.
const page = readFileSync(join(__dirname, "index.html"), "utf8");
const source = page.slice(page.indexOf("  const companion = (() => {"), page.indexOf("  // --- speech input"))
  .replace("await import(SDK)", "await loadSdk(SDK)");
const response = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
const deferred = () => {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
};

function setup({ fetch = async () => response({ enabled: true, token: "test", variables: {} }), connect } = {}) {
  const elements = new Map(), sessions = [], logs = [], requests = [];
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, { hidden: true, textContent: "", addEventListener(event, cb) { this[event] = cb; } });
    return elements.get(id);
  };
  const context = {
    $: element,
    view: { incidentId: "inc-1", state: "ALERTING", companionReady: true },
    backend: "http://test",
    log: (text) => logs.push(text),
    convo: { row: () => ({ set() {} }) },
    fetch: async (...args) => { requests.push(args); return fetch(...args); },
    loadSdk: async () => ({ Conversation: { startSession: async (options) => {
      const session = {
        options, ended: false, volume: 1,
        endSession: async () => { session.ended = true; options.onDisconnect(); },
        setVolume: async ({ volume }) => { session.volume = volume; },
        sendContextualUpdate() {},
      };
      sessions.push(session);
      return connect ? connect(session) : session;
    } } }),
  };
  const companion = vm.runInNewContext(source + "\ncompanion;", context);
  return { companion, context, sessions, logs, requests, status: element("companion-status"), retry: element("companion-retry") };
}

test("disabled companion says not listening and retry can connect", async () => {
  let enabled = false;
  const s = setup({ fetch: async () => response({ enabled }) });
  await s.companion.start("inc-1");
  assert.match(s.status.textContent, /off and is not listening/);
  assert.equal(s.retry.hidden, false);
  assert.equal(s.sessions.length, 0);
  enabled = true;
  await s.retry.click();
  assert.match(s.status.textContent, /connected/);
  assert.equal(s.retry.hidden, true);
  assert.equal(s.sessions.length, 1);
});

test("does not request a token while the closing audio is pending", async () => {
  const s = setup();
  s.context.view.companionReady = false;
  await s.companion.start("inc-1");
  assert.equal(s.requests.length, 0);
});

test("microphone denial is visible and permits a retry", async () => {
  let deny = true;
  const s = setup({ connect: (session) => {
    if (deny) throw new Error("microphone denied");
    return session;
  } });
  await s.companion.start("inc-1");
  assert.match(s.status.textContent, /microphone denied/);
  assert.match(s.status.textContent, /Not listening/);
  deny = false;
  await s.retry.click();
  assert.match(s.status.textContent, /Microphone is active/);
});

test("resolving during connection closes the late session without enabling tools", async () => {
  const gate = deferred();
  const s = setup({ connect: async (session) => { await gate.promise; return session; } });
  const pending = s.companion.start("inc-1");
  await new Promise(setImmediate);
  s.context.view = { state: "IDLE", incidentId: null, companionReady: false };
  s.companion.stop();
  gate.resolve();
  await pending;
  assert.equal(s.sessions[0].ended, true);
  const count = s.requests.length;
  const result = JSON.parse(await s.sessions[0].options.clientTools.relay_to_caregiver({ message: "dizzy" }));
  assert.equal(result.sent, false);
  assert.equal(s.requests.length, count);
  assert.doesNotMatch(s.status.textContent, /connected/);
});

test("a stale disconnect does not erase a new incident's session", async () => {
  const s = setup();
  await s.companion.start("inc-1");
  const old = s.sessions[0];
  s.companion.stop();
  s.context.view.incidentId = "inc-2";
  await s.companion.start("inc-2");
  old.options.onDisconnect();
  await s.companion.start("inc-2");
  assert.equal(s.sessions.length, 2);
  assert.match(s.status.textContent, /connected/);
});

test("relay errors reach the agent instead of claiming success", async () => {
  const s = setup({ fetch: async (url) => url.endsWith("/relay")
    ? response({ detail: "blocked" }, 503) : response({ enabled: true }) });
  await s.companion.start("inc-1");
  await assert.rejects(s.sessions[0].options.clientTools.relay_to_caregiver({ message: "dizzy", urgency: "normal" }), /503/);
  assert.equal(s.logs.filter((x) => x.startsWith("Companion relay request result")).length, 0);
});

test("new session respects backend audio already playing", async () => {
  const s = setup();
  s.companion.duck(true);
  await s.companion.start("inc-1");
  assert.equal(s.sessions[0].volume, 0);
  s.companion.duck(false);
  assert.equal(s.sessions[0].volume, 1);
});

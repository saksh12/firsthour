const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");

// The dashboard's StepFun labels are pure functions; run that block of the page on its own.
const page = readFileSync(join(__dirname, "index.html"), "utf8");
const source = page.slice(page.indexOf("  // --- StepFun view"), page.indexOf("  // --- rendering"));
const sf = vm.runInNewContext(source + "\nstepfunView;", {});

const call = (over) => ({ kind: "triage", model: "step-5-preview", ms: 9800, outcome: "ok", fallback: null,
                          reason: null, deadline_s: 45, incident_id: "inc-1", ...over });

test("a successful call shows its kind and measured latency", () => {
  const r = sf.callRow(call({ kind: "rewrite", ms: 6240, deadline_s: 10 }));
  assert.equal(r.pill, "OK");
  assert.equal(r.cls, "ok");
  assert.equal(r.what, "reply rewrite · 6.2 s");
  assert.equal(r.note, "step-5-preview");
});

test("a timeout names the fallback and the deadline", () => {
  const r = sf.callRow(call({ kind: "rewrite", ms: 10003, outcome: "timeout", fallback: "original text",
                              reason: "timeout", deadline_s: 10 }));
  assert.equal(r.pill, "TIMEOUT");
  assert.equal(r.cls, "no");
  assert.equal(r.note, "fallback: original text (timeout 10 s)");
});

test("rejected JSON names the fallback and the reason", () => {
  const r = sf.callRow(call({ outcome: "invalid", fallback: "rules", reason: "bad json" }));
  assert.equal(r.note, "fallback: rules (bad json)");
  assert.equal(sf.callRow(call({ kind: "answer", outcome: "error", fallback: "template", reason: "http 500" })).note,
               "fallback: template (http 500)");
});

test("model badge: off, waiting, pending, done, fallback", () => {
  assert.deepEqual({ ...sf.modelBadge(null, 0) }, { text: "off: rules only", cls: "off" });
  assert.deepEqual({ ...sf.modelBadge({ status: "off", name: null }, 1) }, { text: "off: rules only", cls: "off" });
  assert.equal(sf.modelBadge({ status: "waiting", name: "step-5-preview" }, 0).text, "waiting for her answer");
  assert.equal(sf.modelBadge({ status: "pending", name: "step-5-preview" }, 1).text, "model: pending…");
  assert.deepEqual({ ...sf.modelBadge({ status: "ok", name: "step-5-preview", ms: 9800, record: {}, answers_seen: 2 }, 2) },
                   { text: "step-5-preview, 9.8 s", cls: "ok" });
  assert.deepEqual({ ...sf.modelBadge({ status: "timeout", ms: 45001, deadline_s: 45, reason: "timeout" }, 1) },
                   { text: "fallback: rules (timeout 45 s)", cls: "no" });
  assert.equal(sf.modelBadge({ status: "invalid", reason: "invalid record (1 errors)" }, 1).text,
               "fallback: rules (invalid record (1 errors))");
});

test("a result on fewer answers says so, also while the rerun is pending", () => {
  const m = { status: "pending", name: "step-5-preview", ms: 12000, record: { conscious: true }, answers_seen: 1 };
  assert.equal(sf.modelBadge(m, 3).text, "model: pending… · saw 1 of 3 answers");
  assert.equal(sf.modelBadge({ ...m, status: "ok" }, 3).text, "step-5-preview, 12.0 s · saw 1 of 3 answers");
});

test("rows show rules, the model's own answer and the record in use side by side", () => {
  const rules = { conscious: true, head_strike: null, pain_location: null, can_move: null, bleeding: null, on_blood_thinner: true };
  const used = { ...rules, head_strike: true };
  const model = { status: "ok", name: "step-5-preview", record: { conscious: true, head_strike: true, pain_location: null,
                  can_move: null, bleeding: false, summary: "x" }, added: ["head_strike"], overruled: ["bleeding"] };
  const rows = sf.rows(used, rules, model, ["ok", "head"]);
  const by = Object.fromEntries(rows.map((r) => [r.label, r]));
  assert.deepEqual([...by["Hit head"].rules], ["—", "v-unk"]);
  assert.deepEqual([...by["Hit head"].model], ["Yes", "v-yes"]);
  assert.deepEqual([...by["Hit head"].used], ["Yes", "v-yes"]);
  assert.equal(by["Hit head"].added, true);
  assert.deepEqual([...by["Bleeding"].model], ["No", "v-no"]);
  assert.equal(by["Bleeding"].overruled, true);
  assert.deepEqual([...by["Bleeding"].used], ["Not said", "v-unk"]);
  assert.deepEqual([...by["Blood thinner"].used], ["Yes (profile)", "v-yes"]);
  assert.deepEqual([...by["Blood thinner"].model], ["n/a", "v-unk"]);
});

test("model cells read … while pending with no result yet, — when off", () => {
  const rules = { conscious: true };
  assert.deepEqual([...sf.rows(rules, rules, { status: "pending", record: null }, ["ok"])[0].model], ["…", "v-unk"]);
  assert.deepEqual([...sf.rows(rules, rules, { status: "off", record: null }, ["ok"])[0].model], ["—", "v-unk"]);
  assert.deepEqual([...sf.rows(rules, rules, null, ["ok"])[0].model], ["—", "v-unk"]);
});

test("an answered but unreadable field is Unclear, an unasked one Not said", () => {
  const rec = { conscious: true, head_strike: null, can_move: null };
  const by = Object.fromEntries(sf.rows(rec, rec, null, ["ok", "head"]).map((r) => [r.label, r]));
  assert.equal(by["Hit head"].used[0], "Unclear");
  assert.equal(by["Can move legs"].used[0], "Not said");
});

test("summary source says who wrote it", () => {
  assert.equal(sf.summarySource({ name: "step-5-preview", added: ["summary"], record: {} }), "Summary written by step-5-preview.");
  assert.match(sf.summarySource({ name: "step-5-preview", added: ["head_strike"], record: {} }), /^Summary templated .*not used/);
  assert.equal(sf.summarySource(null), "Summary templated from the record above.");
});

test("caregiver message notes name the rewrite or its fallback", () => {
  const msg = { text: "omw!!", spoken: "Sarah is on her way." };
  assert.equal(sf.messageNote(msg, call({ kind: "rewrite", ms: 6200 })), "read aloud as “Sarah is on her way.” (step-5-preview, 6.2 s)");
  const fell = call({ kind: "rewrite", outcome: "timeout", fallback: "original text", reason: "timeout", deadline_s: 10 });
  assert.equal(sf.messageNote({ text: "omw", spoken: "omw" }, fell), "read aloud as written · fallback: original text (timeout 10 s)");
  assert.equal(sf.messageNote({ text: "omw", spoken: "omw" }, null), "read aloud as written");
  assert.equal(sf.messageNote({ text: "is she ok?", spoken: null, question: true }, call({ kind: "answer", ms: 7400 })),
               "question, answered by text (step-5-preview, 7.4 s)");
  assert.equal(sf.messageNote({ text: "on my way", spoken: null }, null), "read aloud from a fixed template");
});

test("a corrected field is marked, never credited to the model, and an operator unknown reads Unknown", () => {
  const rules = { conscious: true, head_strike: true, pain_location: null, can_move: null, bleeding: null, on_blood_thinner: true };
  const used = { ...rules, head_strike: false, bleeding: null };
  const model = { status: "ok", name: "step-5-preview", record: { conscious: true, head_strike: true, pain_location: null,
                  can_move: null, bleeding: true, summary: "x" }, added: ["bleeding"], overruled: ["head_strike", "bleeding"] };
  const by = Object.fromEntries(sf.rows(used, rules, model, ["ok", "head", "pain", "move", "bleeding"],
                                        { head_strike: false, bleeding: null }).map((r) => [r.label, r]));
  assert.equal(by["Hit head"].corrected, true);
  assert.deepEqual([...by["Hit head"].used], ["No", "v-no"]);
  assert.equal(by["Bleeding"].corrected, true);
  assert.equal(by["Bleeding"].added, false);
  assert.deepEqual([...by["Bleeding"].used], ["Unknown", "v-unk"]);
  assert.equal(by["Can move legs"].corrected, false);
  assert.deepEqual([...by["Can move legs"].used], ["Unclear", "v-unk"]);
});

test("only the five spoken fields can be corrected", () => {
  const rows = sf.rows({ conscious: true, on_blood_thinner: true }, null, null, ["ok"]);
  assert.deepEqual([...rows.map((r) => r.field)], ["conscious", "head_strike", "pain_location", "can_move", "bleeding", null]);
  assert.ok(rows.every((r) => r.corrected === false));
});

test("a yes/no click cycles Yes, No, Unknown", () => {
  assert.equal(sf.nextValue(true), false);
  assert.equal(sf.nextValue(false), null);
  assert.equal(sf.nextValue(null), true);
  assert.equal(sf.nextValue(undefined), true);
});

test("a corrected record says its summary is templated (corrected)", () => {
  const m = { name: "step-5-preview", added: ["summary"], record: {} };
  assert.equal(sf.summarySource(m, { bleeding: false }), "Summary templated (corrected): the operator set a field above.");
  assert.equal(sf.summarySource(m, {}), "Summary written by step-5-preview.");
});

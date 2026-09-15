import test from "node:test";
import assert from "node:assert/strict";
import { rm } from "node:fs/promises";
import { loadContract, createFixture, fixture, executeCall, runTurn, parseArgs, summarize } from "./run.mjs";
import { scenarios, grade } from "./scenarios.mjs";

const contract = await loadContract();
async function setup(t, id) {
  const scenario = scenarios.find(s => s.id === id);
  const data = await createFixture(scenario, "/usr/bin/python3");
  t.after(() => rm(data.root, { recursive: true, force: true }));
  return { ...data, scenario };
}

test("exported schemas exactly equal production narrow schemas and cannot select requester or confirm", () => {
  assert.equal(contract.schema.length, 7);
  for (const schema of contract.schema) {
    const actual = contract.narrowTools.find(t => t.name === schema.name);
    assert.deepEqual(schema.parametersJsonSchema, JSON.parse(JSON.stringify(actual.parameters)));
    assert.equal(schema.parametersJsonSchema.properties.actor, undefined);
    assert.equal(schema.parametersJsonSchema.properties.confirmation_code, undefined);
  }
  assert.throws(() => contract.toolRequest("grocery_list", { action: "confirm_remove" }));
  assert.throws(() => contract.toolRequest("grocery_add", { actor: "+15555550103", items: [{ name: "Milk" }] }));
});

test("offline scripted function call uses real production adapter/backend and exact relay grading", async t => {
  const { root, config, scenario } = await setup(t, "add_en");
  const before = await fixture(root, { action: "snapshot" });
  let count = 0;
  const generate = async contents => {
    if (++count === 1) return { candidates: [{ content: { role: "model", parts: [{ functionCall: { name: "grocery_add", args: { items: [{ name: "milk", quantity: 2, unit: "cartons" }], lang: "en" } }, thoughtSignature: "synthetic-signature" }] } }] };
    assert.equal(contents[1].parts[0].thoughtSignature, "synthetic-signature");
    return { candidates: [{ content: { role: "model", parts: [{ text: contents.at(-1).parts[0].functionResponse.response.reply }] } }] };
  };
  const turn = await runTurn({ prompt: scenario.turns[0], contents: [], contract, config, generate, maxCalls: 2, requestPrefix: "offline" });
  const after = await fixture(root, { action: "snapshot" });
  const result = grade(scenario, before, after, [turn]);
  assert.deepEqual(result.failures, []);
  assert.equal(result.pass, true);
});

test("private writes are isolated and foreign identities cannot be injected", async t => {
  const { root, config, scenario } = await setup(t, "private_add");
  const before = await fixture(root, { action: "snapshot" });
  const output = await executeCall(contract, { name: "grocery_add", args: { scope: "private", store: "Shop", items: [{ name: "apples", quantity: 2 }] } }, config, "private");
  assert.equal(output.result.ok, true);
  const invalid = await executeCall(contract, { name: "grocery_show", args: { view: "list", actor: "+15555550103" } }, config, "foreign");
  assert.equal(invalid.invalid, true);
  const after = await fixture(root, { action: "snapshot" });
  assert.deepEqual(grade(scenario, before, after, [{ calls: [output], reply: output.result.reply }]).failures, []);
});

test("removal preview cannot become native confirmation", async t => {
  const { root, config, scenario } = await setup(t, "remove_preview");
  const before = await fixture(root, { action: "snapshot" });
  const output = await executeCall(contract, { name: "grocery_remove", args: { items: [{ name: "Milk" }] } }, config, "remove");
  assert.equal(output.result.status, "confirmation");
  const invalid = await executeCall(contract, { name: "grocery_list", args: { action: "confirm_remove", confirmation_code: output.result.confirmation_code } }, config, "confirm");
  assert.equal(invalid.invalid, true);
  const after = await fixture(root, { action: "snapshot" });
  assert.deepEqual(before.family.items, after.family.items);
});

test("caps refuse invalid flags and stop extra tools before execution", async t => {
  assert.throws(() => parseArgs(["--model", "model", "--max-calls", "1000"]));
  assert.throws(() => parseArgs(["--model", "../other"]));
  assert.equal(parseArgs(["--dry-run"]).limit, 3);
  const { root, config } = await setup(t, "add_en");
  const before = await fixture(root, { action: "snapshot" });
  const generate = async () => ({ candidates: [{ content: { role: "model", parts: [1, 2].map(n => ({ functionCall: { name: "grocery_add", args: { items: [{ name: `Milk ${n}` }] } } })) } }] });
  const turn = await runTurn({ prompt: "add", contents: [], contract, config, generate, maxCalls: 1, requestPrefix: "limit" });
  assert.equal(turn.exhausted, true);
  assert.equal(turn.calls.length, 0);
  assert.deepEqual((await fixture(root, { action: "snapshot" })).family.items, before.family.items);
});

test("summary reports nearest rank latency and gates distinguish quality from state", () => {
  const row = n => ({ turns: [{ elapsedMs: n }], grade: { pass: n !== 3 } });
  assert.equal(summarize([row(1), row(2), row(3)]).passed, 2);
  assert.equal(summarize([row(1), row(2), row(3)]).p95Ms, 3);
  const state = { family: { items: [], preferences: [] }, private: [] };
  const scenario = { unchanged: true, refusal: true };
  assert.equal(grade(scenario, state, state, [{ reply: "No.", calls: [] }]).refusalLanguageReviewRequired, true);
  assert.ok(grade(scenario, state, state, [{ reply: "PRIVATE_SENTINEL_CAROL", calls: [] }]).failures.includes("private_or_internal_output"));
});

// Wording flexibility must not quietly become an automatic correctness pass.
test("conversational rephrasing needs review while state failures remain hard failures", () => {
  const state = { family: { items: [], preferences: [] }, private: [] };
  const scenario = { unchanged: true, lang: "en" };
  const turns = [{ reply: "Sure! 🛒 Nothing on your list yet.", calls: [{ result: { ok: true, status: "done", reply: "Nothing needed." } }] }];
  const result = grade(scenario, state, state, turns);
  assert.equal(result.deterministicPass, true);
  assert.equal(result.pass, false);
  assert.deepEqual(result.failures, ["manual_reply_review_required"]);
  const changed = { family: { items: [{ name: "unrequested", household: "Synthetic family A" }], preferences: [] }, private: [] };
  const failure = grade(scenario, state, changed, turns);
  assert.equal(failure.deterministicPass, false);
  assert.ok(failure.failures.includes("family_items_changed"));
});

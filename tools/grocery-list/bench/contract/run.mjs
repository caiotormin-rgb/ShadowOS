#!/usr/bin/env node
import { execFile } from "node:child_process";
import { mkdtemp, mkdir, readFile, writeFile, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createHash } from "node:crypto";
import { performance } from "node:perf_hooks";
import { scenarios, grade } from "./scenarios.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "../..");
const ACTOR = "+15555550101";


export function parseArgs(args) {
  const options = { limit: 3, reps: 1, maxCalls: 4, maxRequests: 40, python: "/usr/bin/python3", dryRun: false, output: resolve("grocery-contract-results") };
  const fields = { "--model": "model", "--limit": "limit", "--reps": "reps", "--max-calls": "maxCalls", "--max-requests": "maxRequests", "--python": "python", "--output": "output" };
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--dry-run") { options.dryRun = true; continue; }
    const field = fields[args[i]];
    if (!field || !args[i + 1]) throw new Error(`Unknown or incomplete option: ${args[i]}`);
    options[field] = ["limit", "reps", "maxCalls", "maxRequests"].includes(field) ? Number(args[++i]) : args[++i];
  }
  for (const [field, ceiling] of [["limit", scenarios.length], ["reps", 10], ["maxCalls", 8], ["maxRequests", 200]]) {
    if (!Number.isInteger(options[field]) || options[field] < 1 || options[field] > ceiling) throw new Error(`${field} must be 1..${ceiling}`);
  }
  if (!options.dryRun && !/^[a-zA-Z0-9._-]+$/.test(options.model ?? "")) throw new Error("Supply an explicit --model MODEL (no inferred model or production change)");
  return options;
}

export async function loadContract() {
  const built = join(ROOT, "plugin/dist/index.js");
  if ((await stat(built)).mtimeMs < (await stat(join(ROOT, "plugin/src/index.ts"))).mtimeMs) throw new Error("Build the Grocery plugin first; dist/index.js is stale");
  const plugin = await import(pathToFileURL(built));
  for (const name of ["narrowTools", "toolRequest", "runAgentApi"]) if (!plugin[name]) throw new Error(`Production export missing: ${name}`);
  const schema = plugin.narrowTools.map(t => ({ name: t.name, description: t.description, parametersJsonSchema: JSON.parse(JSON.stringify(t.parameters)) }));
  const instruction = (await readFile(join(ROOT, "skill/SKILL.md"), "utf8")).replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, "").trim();
  const instructionHash = createHash("sha256").update(instruction).digest("hex");
  const hash = createHash("sha256").update(JSON.stringify(schema)).digest("hex");
  return { ...plugin, schema, hash, instruction, instructionHash };
}

export async function fixture(root, request, python = "/usr/bin/python3") {
  const payload = JSON.stringify({ root, ...request });
  // execFile's promisified API has no stdin option; pass data via the child stream.
  return new Promise((accept, reject) => {
    const child = execFile(python, [join(HERE, "fixtures.py")], { timeout: 15000, maxBuffer: 1024 * 1024 }, (error, stdout) => {
      if (error) reject(new Error("Synthetic fixture operation failed", { cause: error }));
      else { try { accept(JSON.parse(stdout)); } catch (e) { reject(e); } }
    });
    child.stdin.end(payload);
  });
}

export async function createFixture(scenario, python) {
  const root = await mkdtemp(join(tmpdir(), "grocery-contract-"));
  await writeFile(join(root, ".synthetic-fixture"), "Synthetic benchmark data only\n", { mode: 0o600 });
  await fixture(root, { action: "seed", lang: scenario.lang, items: scenario.seed }, python);
  return { root, config: { pythonPath: python, scriptPath: join(ROOT, "core/grocery.py"), familyDbPath: join(root, "family.sqlite3"), privateDbDir: join(root, "private"), whatsappAccountId: "synthetic-contract", allowedRequesters: [ACTOR] } };
}

export async function executeCall(contract, call, config, requestId) {
  try {
    const request = contract.toolRequest(call.name, call.args ?? {});
    const result = await contract.runAgentApi(request, ACTOR, config, { requestId });
    return { name: call.name, args: call.args ?? {}, result };
  } catch {
    return { name: call.name, args: call.args ?? {}, invalid: true, result: { ok: false, status: "error", reply: "That tool or input is unavailable.", assumptions: [] } };
  }
}

export async function runTurn({ prompt, contents, contract, config, generate, maxCalls, requestPrefix }) {
  contents.push({ role: "user", parts: [{ text: prompt }] });
  const started = performance.now();
  const calls = [], usage = [];
  let reply = "", exhausted = false;
  // At most maxCalls tool calls plus one final response generation.
  for (let round = 0; round <= maxCalls; round++) {
    const response = await generate(contents);
    if (response.usageMetadata) usage.push(response.usageMetadata);
    const content = response.candidates?.[0]?.content;
    if (!content?.parts?.length) throw new Error("Provider returned no candidate content");
    contents.push(content); // Keep provider thought signatures unchanged for the next round.
    const functions = content.parts.filter(p => p.functionCall).map(p => p.functionCall);
    reply = content.parts.filter(p => p.text && !p.thought).map(p => p.text).join("");
    if (!functions.length) return { reply, calls, usage, elapsedMs: performance.now() - started, exhausted };
    if (calls.length + functions.length > maxCalls) { exhausted = true; break; }
    const parts = [];
    for (const call of functions) {
      const output = await executeCall(contract, call, config, `${requestPrefix}:${calls.length}`);
      calls.push(output);
      parts.push({ functionResponse: { name: call.name, ...(call.id ? { id: call.id } : {}), response: output.result } });
    }
    contents.push({ role: "user", parts });
  }
  return { reply, calls, usage, elapsedMs: performance.now() - started, exhausted: true };
}

export function summarize(rows) {
  const latencies = rows.flatMap(r => r.turns.map(t => t.elapsedMs)).filter(Number.isFinite).sort((a, b) => a - b);
  const percentile = p => latencies.length ? latencies[Math.max(0, Math.ceil(p * latencies.length) - 1)] : null;
  return { scenarios: rows.length, passed: rows.filter(r => r.grade.pass).length, passRate: rows.length ? rows.filter(r => r.grade.pass).length / rows.length : null, medianMs: percentile(.5), p95Ms: percentile(.95), refusalLanguageReviews: rows.filter(r => r.grade.refusalLanguageReviewRequired).length, replyReviews: rows.filter(r => r.grade.replyReviewRequired).length };
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const contract = await loadContract();
  await mkdir(options.output, { recursive: true, mode: 0o700 });
  const runId = new Date().toISOString().replace(/[:.]/g, "-");
  const output = join(options.output, `${runId}-${options.model ?? "dry-run"}`);
  await mkdir(output, { mode: 0o700 });
  await writeFile(join(output, "schema.json"), JSON.stringify(contract.schema, null, 2));
  const metadata = { kind: "direct-gemini-contract-evaluation", endToEndWhatsApp: false, runtime: process.version, model: options.model ?? null, schemaHash: contract.hash, options, systemInstruction: contract.instruction, instructionHash: contract.instructionHash, createdAt: new Date().toISOString(), productionModelChanged: false };
  await writeFile(join(output, "metadata.json"), JSON.stringify(metadata, null, 2));
  if (options.dryRun) { console.log(JSON.stringify({ output, dryRun: true, schemaHash: contract.hash, scenarios: scenarios.slice(0, options.limit).map(s => s.id) })); return; }
  const apiKey = process.env.GEMINI_API_KEY ?? process.env.GOOGLE_API_KEY;
  if (!apiKey) throw new Error("Set GEMINI_API_KEY or GOOGLE_API_KEY in the invoking environment; credentials are never read from disk");
  let requests = 0;
  const generate = async contents => {
    if (++requests > options.maxRequests) throw new Error("Global request budget exhausted");
    const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${options.model}:generateContent`, {
      method: "POST", headers: { "Content-Type": "application/json", "x-goog-api-key": apiKey }, signal: AbortSignal.timeout(60000),
      body: JSON.stringify({ systemInstruction: { parts: [{ text: contract.instruction }] }, contents, tools: [{ functionDeclarations: contract.schema }], toolConfig: { functionCallingConfig: { mode: "AUTO" } }, generationConfig: { temperature: 0, maxOutputTokens: 2048 } }),
    });
    if (!response.ok) throw new Error(`Gemini HTTP ${response.status}; no automatic retry`);
    return response.json();
  };
  const rows = [];
  for (let rep = 0; rep < options.reps; rep++) for (const scenario of scenarios.slice(0, options.limit)) {
    const { root, config } = await createFixture(scenario, options.python);
    try {
      const before = await fixture(root, { action: "snapshot" }, options.python);
      const turns = [], contents = [];
      for (let i = 0; i < scenario.turns.length; i++) {
        try { turns.push(await runTurn({ prompt: scenario.turns[i], contents, contract, config, generate, maxCalls: options.maxCalls, requestPrefix: `${runId}:${rep}:${scenario.id}:${i}` })); }
        catch (error) { turns.push({ reply: "", calls: [], usage: [], error: error.message }); break; }
      }
      const after = await fixture(root, { action: "snapshot" }, options.python);
      const row = { scenario: scenario.id, repetition: rep + 1, lang: scenario.lang, before, after, turns, grade: grade(scenario, before, after, turns) };
      rows.push(row);
      await writeFile(join(output, "results.json"), JSON.stringify(rows, null, 2), { mode: 0o600 });
      console.log(JSON.stringify({ scenario: row.scenario, repetition: row.repetition, ...row.grade }));
    } finally { await rm(root, { recursive: true, force: true }); }
  }
  const summary = { ...summarize(rows), requests: Math.min(requests, options.maxRequests), output, schemaHash: contract.hash, evidenceScope: "Synthetic direct API tool contract; not WhatsApp routing, real authorization, full production prompts, or production model approval." };
  await writeFile(join(output, "summary.json"), JSON.stringify(summary, null, 2));
  console.log(JSON.stringify(summary, null, 2));
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main().catch(error => { console.error(error.message); process.exitCode = 1; });

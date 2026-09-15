# Narrow Grocery contract evaluation

This is a synthetic **direct Gemini API** evaluation, not an end-to-end OpenClaw/WhatsApp benchmark. It imports the built production plugin's `narrowTools`, `toolRequest`, and `runAgentApi`; tools execute against fresh temporary SQLite fixtures through the real backend. No live database, channel send, OAuth copy, or runtime agent is used.

The purpose is to detect model/schema/backend compatibility problems cheaply before a restricted WhatsApp pilot. Passing does not justify downgrading production. The production baseline remains unchanged. Full prompt, channel identity, host policy invocation, inter-domain transitions, retries, media, real-member language preferences, and runtime model selection require separate evaluation.

## Preparation and offline checks

Build the current Grocery plugin using its documented build command. The harness refuses stale `dist/index.js` compared with `src/index.ts`; it never rebuilds behind your back. Use Node 24 and Python 3:

```sh
node --test tools/grocery-list/bench/contract/test.mjs
node tools/grocery-list/bench/contract/run.mjs --dry-run --limit 14 --output /tmp/grocery-contract-dryrun
```

Seven offline tests cover the current conversational contract. They cover exact production schema parity, real adapter/backend execution with fake provider responses, private isolation, malicious actor arguments, removal preview, tool-call caps, and grading. `--dry-run` exports exact schemas and canonical instructions without requiring a credential or making any request.

The prompt comes from `tools/grocery-list/skill/SKILL.md`, with YAML frontmatter removed. Metadata records that text and its SHA-256 alongside the tool-schema hash, explicit model, Node version and command options. Tool definitions use `parametersJsonSchema` containing the production schema unchanged; no hand-transcribed model-facing schema exists. Tool arguments are validated by the production `toolRequest` before execution. The runner preserves Gemini thought signatures between rounds.

## Deliberately bounded provider run

The operator supplies an already authorized key via `GEMINI_API_KEY` or `GOOGLE_API_KEY` in the invoking environment. The runner never reads credential files, prints keys, or puts keys in URLs. It uses the `x-goog-api-key` header only. No provider requests were made while building/testing this harness.

```sh
node tools/grocery-list/bench/contract/run.mjs \
  --model EXACT_GEMINI_MODEL_ID \
  --limit 3 --reps 1 --max-calls 4 --max-requests 20 \
  --output /tmp/grocery-contract-results
```

`--model` is required for real calls; no default provider model is inferred. `--limit` selects the first N of 14 scenarios, `--reps` repeats with completely fresh databases, and `--max-calls` caps executed tools per user turn. `--max-requests` globally caps HTTP requests; there are no automatic retries. Defaults: three scenarios, one repetition, four tool calls/turn, forty HTTP requests. Hard ceilings: fourteen scenarios, ten repetitions, eight calls/turn, two hundred HTTP requests. Each request times out after sixty seconds. At most max-calls plus one generations occur in a turn. More calls than the remaining budget cause termination before any tools in that response execute. Scenarios run sequentially for interpretable latency and controlled spend.

Use `--limit 14` for all coverage after a small compatibility probe. `--python /absolute/python` overrides `/usr/bin/python3`. Interrupted/failed provider turns count as incomplete; request-budget failures are recorded and remaining fixture cleanup still runs. Provider HTTP errors report only status codes. Outputs are under a new timestamp/model directory; they never overwrite a previous run.

## Fixtures and oracles

Each scenario has two synthetic households and three fake identities. Another household contains `PRIVATE_SENTINEL_CAROL`. Alice's shared defaults are set to Shop and the scenario language; private preferences are **not** seeded. The private-add scenario explicitly names Shop, matching first-use private behavior without inventing a default store. All DB roots are newly created `/tmp/grocery-contract-*` directories with a marker; the fixture helper refuses arbitrary database locations. Roots are cleaned after each scenario, while before/after snapshots are retained in results.

Fourteen scenarios cover EN/PT add, purchased/needed reversal, rendered lists, removal preview, refusing self-confirmation, ambiguous names, private writes, foreign-household requests, admin escalation, saving default-store preference, and a second store. No native removal confirmation is available to the model.

Deterministic grading checks intended item attributes and exact intended family row counts, unchanged state when required, private isolation, other-household items/trips/preferences, explicit tool errors, invalid/unavailable tool attempts, tool status, reply content, backend-reply fidelity review, wrong explicitly requested language, internal/identity disclosure, and incomplete turns. Foreign-household and admin refusals must leave state unchanged and call no tools. **Refusals remain failed/pending (`manual_refusal_review_required`) until a human checks semantic correctness and language**; a nonempty sentence alone cannot pass. `deterministicPass` separates structural checks from that review requirement. Non-refusal localization is grounded in the backend reply and scenario language; review generated text if it bypassed or changed the backend wording.

Name matching permits case/accents/plural substrings to avoid scoring harmless normalization differences as failures. This is a limited oracle, not semantic proof. The sample is small, there are no confidence bounds, and aggregate pass rate cannot hide any safety failure. Review `results.json` as well as `summary.json`; do not compare these timings directly with historical CLI/WhatsApp results.

Artifacts: `schema.json`, `metadata.json` (including canonical system text), `results.json` (calls, sanitized backend output, before/after state, per-turn usage and timing, failures), and `summary.json` (pass count, nearest-rank median/p95, requests). All data is synthetic. Read-only provider refusal cases still require manual language review. Token usage is reported as returned by the provider, without a guessed pricing conversion.

## Conversational reply revision

After live user feedback, exact relay is no longer a product requirement. The
assistant may phrase replies naturally and use emoji bullets, while preserving
all facts, uncertainty, partial outcomes and exact confirmation commands.
`relayExact` remains a diagnostic. A changed reply is pending manual review
(`manual_reply_review_required`), not automatically correct and not a wording
defect merely because it differs. State/authority failures remain hard failures.
Earlier recorded pilot results used the old strict contract and are historical;
do not compare aggregate pass counts across prompt/grader versions.

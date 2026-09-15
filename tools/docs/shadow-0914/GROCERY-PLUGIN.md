# Grocery plugin and canonical skill handoff — 2026-09-14

## State and ownership

Implemented in the isolated `shadow-0914` worktree by the Grocery plugin lane.
No production config, database, dependency install, restart or user send occurred.
The worktree reuses installed node_modules through an ignored symlink; compiled
`dist` is local build output, not a source artifact. Root coordinates commit and
rollout. Core API and native router are separate parallel lanes.

Owned source: `tools/grocery-list/plugin/**`, `tools/grocery-list/skill/**` and
this document. The plugin uses existing TypeBox/OpenClaw APIs and the existing
AccessClient; no new provider, router framework or policy authority was added.

## Before and after

The old registered `grocery_list` carried all action-specific fields in one
schema, invoked the legacy CLI, and returned database-shaped output for most
actions. Removal execution depended on prose confirmation. The new registered
execution paths all call the sibling `core/agent_api.py`, using seven narrower
schemas and a public reply envelope. The legacy `grocery_list` name stays
optional and follows this safe API path; omit it from new allowlists.

| Tool | Required selector/input | Backend action |
|---|---|---|
| grocery_show | view | list/stores/due/history/layout/help/onboard |
| grocery_add | items objects | add |
| grocery_mark | items name objects, state | buy/unbuy |
| grocery_remove | items name objects | remove preview |
| grocery_trip | operation | close/reopen |
| grocery_activity | optional filters | activity |
| grocery_preferences | optional own preferences | preferences |

Common fields are scope, store and lang. Add carries quantities, units, notes,
product URLs and optional capture metadata. Mark/remove use one common unit
when the user specified it. Activity's `item` is a query filter, while item
mutations consistently use `items`. Preferences supports lang/store/name/timezone
and store walkOrder. Section and walk-order enums reuse the established schema.

The canonical skill now describes the actual tools and honest capabilities:
no URL fetch tool is promised; only host-supplied media extraction or explicitly
provided product names can become products. Partial names pass to the engine.
On mixed-success clarification, resubmit only unresolved items. Reply includes
assumptions once; relay it verbatim. Preferences are acknowledged after a
successful persistence call, not merely after a conversational promise.

## Identity, confirmation and access

Every factory requires exact channel `whatsapp`, the configured account ID, and
a requester on the plugin allowlist. Accepted sender forms are E.164/plain
digits, a WhatsApp prefix, or a personal `@s.whatsapp.net` JID. Group JIDs,
`@lid`, arbitrary text with digits and missing accounts are rejected. The
restrictive parser is deliberate; expanding transport formats requires evidence
and tests, not digit stripping.

Neither narrow schemas nor the legacy schema expose actor, DB, request_id or
native confirmation authority. Execution validates the schema again. The bridge
selects configured family storage or an actor-hashed private path. Trusted
`--private` lets the API provision/bind only the private owner; the API rejects
other members and requires enrolled membership for family storage.

`grocery_remove` only previews. Native `/remover CODE` checks host
`isAuthorizedSender === true`, exact account and sender plus the allowlist.
The native `from` identity must match the same personal sender; missing targets
and group conversations fail closed. Only then does it invoke `confirm_remove` with `--trusted-confirmation`. Complete
command matching refuses extra words. Private previews supply
`/remover CODE private`, selecting the same isolated private DB. The backend
handles expiry, actor ownership, stale snapshots and single use. Mode switching
does not approve or cancel previews; confirmations remain explicit native
commands and never run through an LLM tool.

With optional `accessDbPath`, the command dynamically imports the sibling Access
plugin's built client, resolves an active person and checks `grocery.use` on
every attempt. Revoked grants, removed persons, missing stores and missing
modules deny immediately. Even an owner needs the grant on this command path.
This config must be supplied by the pilot rollout. Omission keeps legacy
allowlist behavior and must not be described as access-grant enforcement.

## Runtime/public contract

Configured scriptPath remains the legacy `core/grocery.py`; agent_api.py is
resolved beside it. Execution is fixed `execFile(python, argv)` without a shell,
15-second timeout and 256 KiB stdout buffer. JSON keeps product text out of
command syntax and passes lang on every operation. Public output is compact JSON
containing `{ok,reply,status,assumptions,candidates?,confirmation_code?}`. Unknown
backend fields and candidate internals are removed. Subprocess stderr,
tracebacks and malformed envelopes produce a fixed localized failure reply.

The old exported `runGrocery`, `groceryArguments`, `compactActivity` and loose
`normalizePhone` remain legacy CLI/test utilities. Registered tools do not call
them (except actor-hashed databasePath receives an already validated actor).
New consumers must use runAgentApi. These compatibility utilities are not new
model capabilities; their eventual removal should be a separate migration.

Host session ID/key plus tool-call ID are hashed into request_id. The API owns
atomic receipts. This guards repeated execution of one identical host call.
The installed SDK lacks inbound WhatsApp message ID in tool factory context;
repeated user messages or newly generated model call IDs are NOT deduplicated
by the plugin. Session-less diagnostic calls omit request IDs. Do not claim
exactly-once WhatsApp delivery. Root evaluation harness can supply its own
synthetic stable receipt IDs.

## Build, metadata and evaluation exports

Mixed tool/command plugins use `definePluginEntry`, so the previous tool-only
manifest generator is no longer the build path. `plugin:build` compiles;
`plugin:validate` compiles and runs the test suite, including manifest/registration
consistency. The checked-in manifest declares all tool names optional. Build
Access before exercising accessDbPath. Deploy compiled plugin, manifest, core
API and canonical skill as one compatible release.

Evaluation exports:
- `narrowTools`: name, description, parameters and request mapping.
- `toolRequest(name,input)`: pure production-schema validation and mapping.
- `runAgentApi(request,requester,config,{requestId?,exec?})`: production bridge.
- `removeCommand(context,config,exec?)`: native handler for synthetic tests only.

Direct bridge evaluations bypass OpenClaw's factory context and routing. Report
them as schema/backend model evaluations, not end-to-end WhatsApp results. Never
point them at production DBs or use actual household identities.

## Validation completed

`npm run plugin:validate` passed TypeScript and **31 tests** (22 legacy plus 9
new focused tests). New tests cover:
- optional registration/manifest agreement and authenticated native command;
- field validation, language propagation, consistent items and authority refusal;
- exact account/sender identity and group/alternate namespace rejection;
- fixed subprocess argv and same-call receipt identity;
- compact output and safe malformed-response/subprocess errors;
- confirmation authority unavailable to model calls;
- immediate active-grant revocation and removed-person refusal;
- private storage and correctly scoped private confirmation command;
- real synthetic private preferences → add → same-call retry → list → removal
  preview → authenticated native confirmation → item absence.

All DBs created in temporary directories and removed after tests. No provider
model calls were made in this lane. Root owns comparative model testing,
restricted-runtime pilot verification and deployment readiness.

## Integration/rollout checks for root

1. Allowlist only the seven narrow names in Grocery mode; no grocery_list.
2. Include native command availability and grants in the pilot manifest.
3. Verify actual WhatsApp sender/account host contexts before widening rollout.
4. Set accessDbPath and deploy built AccessClient alongside both household tools.
5. Deploy the canonical skill without retaining contradictory old instructions
   in workspace AGENTS.md; the skill alone cannot correct deployment drift.
6. Keep existing production model until comparative evidence supports a change.
7. Preserve prior plugin/core/skill/config together for rollback. Never delete
   confirmation/receipt tables as a rollback shortcut; old CLI ignores them.

## Integration review follow-up

A later cross-lane review added the direct native `from === sender` check above
and the same guard, strict transport parsing, and exact code-only syntax to
Doctor `/ok`. Both command test suites now cover live access grant success,
revocation and removed persons with synthetic databases. The new
`tools/household-config/test/integration.mjs` passes two provider-free composition
tests using the actual OpenClaw 2026.9.4 context builder and registered
router/access/Grocery callbacks. See REVIEW.md for scope and limitations.

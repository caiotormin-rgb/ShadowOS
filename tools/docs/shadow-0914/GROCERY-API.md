# Grocery member API — 2026-09-14

Implementation: `tools/grocery-list/core/agent_api.py`.
Also owned in this lane (approved during review): the household event-filter fix in `tools/grocery-list/core/stores.py`.
Focused tests: `tools/grocery-list/core/tests/test_agent_api.py`.
Status: implemented and tested in the isolated `shadow-0914` worktree; this document does not mean production was deployed.

## Why this adapter exists

The existing Python core already knows how to resolve stores, deduplicate English/Portuguese product names, match partial names, record events, and render member replies. The old bridge sometimes returned complete database records and relied on the model to reconstruct the expected response. The new API returns the rendered member reply plus only the state needed to continue a conversation. Existing operator CLI behavior is unchanged.

Review also found that the existing last-used-store fallback filtered store names by household but did not filter the event actor household. Two households with the same store name could therefore steer each other’s default-store choice. `stores.resolve_store` now also requires `events.group_id` to match. A regression creates overlapping store names and verifies another household’s later activity cannot influence the caller’s default.

No new external framework was needed: the adapter reuses this repository's SQLite domain functions and renderers. It does not introduce a second grocery engine, a network client, or a model call.

## Invocation and authority

```
python3 agent_api.py --db /trusted/path/list.sqlite --actor '+15555550101' \
  --request-json '{"action":"list","store":"Shop","lang":"en"}'
```

The trusted bridge derives both the database path and actor from the authenticated channel/session context. **Neither may come from model arguments.** The request cannot change actor, group membership, DB path, sharing recipients, or account permissions. Supplying those extra JSON keys has no authority.

Every shared request requires an enrolled actor, including help/onboard/preferences. This deliberately differs from the legacy operator CLI's unconfigured-household fallback. Membership is checked before store lookup or receipt replay. Household selection otherwise preserves the existing deterministic first-group behavior; this change does not introduce selectable multiple household membership.

`--private` is exclusively for the trusted bridge's actor-hashed private database path. On first use it binds that database to the actor and enrolls that actor. It supports existing private DBs with items but no member registry. It refuses a database already bound to another actor, or containing any other actor's membership. Subsequent shared-path calls to an already private-bound database also refuse another actor. The private path construction remains a bridge responsibility: this flag cannot prove that an arbitrary path is actually private.

`--trusted-confirmation` is exclusively for the authenticated **native inbound** `/remover CODE` command. It is an authority assertion by the bridge, not cryptographic proof. The model tools must never expose or forward this flag and must reject `confirm_remove` as a model action. Local code able to execute arbitrary shell commands retains operator-level authority; a CLI flag cannot sandbox that operator.

## Request and reply contract

Every response is JSON:

```json
{"ok":true,"reply":"Already rendered user-facing text","status":"done","assumptions":[]}
```

`status` is `done`, `clarification`, `confirmation`, or `error`. `ok` is false only for errors. A clarification may include completed changes for other unambiguous items, following the existing core's behavior; its reply and assumptions state that outcome. It does not mean the whole batch changed nothing.

Optional `candidates` contains only member-facing names/units (or the core's `{name,candidates:[names]}` choice groups). Optional `confirmation_code` identifies a removal preview. No raw item rows, actor identities, source text, SQLite paths, stack traces, or subprocess errors are returned.

The bridge receives a complete, conversational `reply`. Consequential item matches and unresolved choices are explained in that reply; routine store-fallback mechanics remain only in `assumptions` because the chosen store is already named. The separate lists are machine-readable context, not additional text to append. Do not send the JSON itself to users. The user-requested conversational refresh and examples are documented in `GROCERY-REPLIES.md`; follow the current skill for permitted connective wording.

| Action | Request fields | Result |
| --- | --- | --- |
| `add` | `items` strings or `{name,quantity?,unit?,note?,productUrl?}`, optional `store`, `sourceType`, `sourceRef`, `rawText` | Localized compact add/update confirmation |
| `list` | `store?`, `neededOnly?` | Conversational localized list with emoji bullets, optional purchased section, and aisle headings for longer lists |
| `buy`, `unbuy` | `items` names or `{name}`, `unit?`, `store?`, optional source fields | Confirmation, assumptions, unresolved choices when applicable |
| `remove` | `items`, `unit?`, `store?` | Preview only; never removes items |
| `confirm_remove` | `confirmation_code` (alias `code`) | Native command only; validates and consumes preview |
| `close`, `reopen` | `store?`; `tripId?` for reopen | Existing trip close/reopen behavior, rendered |
| `history` | `store?`, `limit?` (1–50) | Compact archive text with requester-local dates, public closer names, quantities, notes, product URLs, and localized outcomes |
| `stores` | none | This household's store names |
| `due` | `store?`, `section?`, `slack?` | Existing deterministic due renderer |
| `layout` | `store?`, `walkOrder?` (alias `layout`) | Read or update store section order |
| `help`, `onboard` | none | Existing localized guide |
| `activity` | `store?`, `since?`, `until?`, `by?`, `item?`, `limit?`, `changeType?` (alias `eventAction`) | Existing member-safe activity text |
| `preferences` | `lang?`, `store?`, `name?`, `timezone?`, `walkOrder?` | Reads current preferences when no fields supplied; otherwise updates only the authenticated requester and preserves omitted name/store |

All actions accept optional `lang: en|pt`. Otherwise the existing requester preference wins. Naming a future default store is permitted; its eventual creation is still scoped to the requester's household. Timezone uses existing IANA validation and `auto` behavior. Optional `preferences.walkOrder` also updates the selected store layout (shared within that household); personal language/name/timezone/default-store remain requester-scoped. No media fetching is performed here: only already normalized items and optional validated product URLs reach storage.

Compatibility aliases `names`, `item`, `needed_only`, and `trip_id` are accepted internally. New model schemas should use the consistent public shape.

## Removal lifecycle

1. The model requests `remove` with the user's words.
2. Strict existing exact/canonical matching resolves each item. Partial or unit-ambiguous matches produce a clarification rather than silently selecting an item.
3. The API snapshots full item rows and the store's event sequence, creates a random eight-hex-character token, and replaces that requester's previous preview.
4. The user receives a full preview and `/remover CODE` instruction. Tokens expire after five minutes.
5. Only the native command bridge calls `confirm_remove` with `--trusted-confirmation` and the same authenticated actor and database scope.
6. The API rechecks enrollment, token actor/group, expiry, unused status, event sequence, and all row fields inside one SQLite write transaction.
7. It removes exactly those rows, records events with existing functions, consumes the token, stores a retry receipt if supplied, and commits once.

Any intervening event in that store invalidates the preview, even an unrelated item change. This conservative choice catches edits that are immediately reverted and timestamp collisions. Edits in other stores do not invalidate it. Full row checks also catch direct SQL changes that omitted events. Layout/preferences changes do not change the item selection.

A stale-token error leaves the token unusable while its snapshot remains stale; it expires normally. New removal requests replace old previews. Commands for a different person or wrong private/shared scope cannot consume a preview. Plain `yes`, `sim`, model-generated tool arguments, and ordinary remove calls cannot authorize deletion.

## Retry behavior and transaction ownership

The optional **trusted bridge supplied** `request_id` is a stable call identity, maximum 256 characters. Never expose it as an LLM argument. Receipts are scoped by actor and database; their digest includes current household ID and the request payload. Same ID and same request within 24 hours returns the original envelope. Same ID with different request or household returns an error. Expired receipts are deleted on successful requests.

The current plugin can derive an identity from session key + tool call ID. This protects retries of that specific call. It does **not** deduplicate a redelivered channel message for which the runtime creates a different tool call ID. End-to-end delivery deduplication requires a stable inbound message ID from the runtime; do not claim that guarantee until it is integrated.

Existing domain helpers call `commit()`. `_Transaction` delegates all connection operations except those commits, so `handle()` owns the encompassing `BEGIN IMMEDIATE` transaction. Domain mutation, confirmation consumption, and receipt storage commit together. An exception while preparing the result or receipt rolls back the mutation. This avoids the gap where a mutation succeeds but a crash before receipt storage causes a retry to run it again. It also makes a multi-item failed operation atomic where the domain helper itself has not returned a partial-success result.

These two support tables are additive and do not change the existing schema version:

- `agent_confirmations`: actor/group-scoped removal snapshot, expiration, used flag.
- `agent_receipts`: actor/request ID, payload digest, expiration, rendered envelope.

Private databases also receive `agent_private_owner`. The stored confirmation rows include original item data for up to five minutes; receipt replies persist up to 24 hours. Cleanup happens on successful API requests; idle databases retain expired rows until the next successful request. No raw inbound message text is added to these tables.

## Validation and known limits

Focused tests cover output shape, same-household sharing, cross-household isolation, private binding, enrollment denial, native confirmation authority, actor mismatch, token replay/expiry, changed snapshots, ambiguity, requester-only preferences, receipt replay/conflict and concurrent duplicate calls, household changes before receipt replay, edited-then-reverted snapshots, rollback after domain writes, every common read action, and trip close/reopen.

Run from repository root:

```
PYTHONPATH=tools/grocery-list/core python3 -m unittest discover -s tools/grocery-list/core/tests -p test_agent_api.py -v
PYTHONPATH=tools/grocery-list/core python3 -m unittest discover -s tools/grocery-list/core/tests -v
```

Generic errors intentionally omit raw core exception strings. For now they may be less specific than the legacy CLI's operator errors. Add structured domain error types before expanding member-facing error explanations; do not simply echo exception text.

The output scrubber additionally masks registered actor strings, phone-shaped text, and local system path patterns. It is defense in depth, not a general-purpose secret classifier; proper household/private scoping and avoiding raw rows remain the actual privacy boundary. Product names, notes, and URLs remain user-controlled grocery data.

Read operations currently also take a write transaction because they may initialize adapter tables and record receipts. The existing CLI remains available for standalone operator reads. Measure concurrency before increasing traffic; don't infer production latency from these local synthetic tests.

### Recorded verification

- Full Grocery core suite: **392 tests passed** after the store isolation and skill integration fixes.
- Final focused adapter suite: **21 tests passed**, including two subsequently added contract checks for `changeType` forwarding and read-only preference lookup.
- `git diff --check` and Python bytecode compilation passed.
- Tests used temporary synthetic databases only. No live DB, gateway configuration, or real-user messaging was touched by this lane.

An earlier full-suite invocation from repository root lacked the core directory on `PYTHONPATH`; use the commands above or run discovery from `tools/grocery-list/core`. An archive test initially used an old timestamp and correctly triggered the existing retention purge; its fixture now uses a future synthetic timestamp to test rendering independently of retention. Neither observation required weakening a production safeguard.

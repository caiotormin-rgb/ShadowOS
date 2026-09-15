# Grocery replies: conversational refresh

## Trigger and scope

The user reported that Grocery replies felt horrible and asked for more conversational language with emoji bullets. This change updates the member API's ready-to-send presentation, keeping deterministic item selection, state changes, authorization, and confirmation handling intact.

Owned files:

- `tools/grocery-list/core/member_render.py` — new presentation-only helpers.
- `tools/grocery-list/core/agent_api.py` — uses those helpers for member replies.
- `tools/grocery-list/core/tests/test_agent_api.py` — revised wording assertions and six new user-facing regressions.
- This document and the response-contract paragraph in `GROCERY-API.md`.

The legacy `render.py` and CLI stay unchanged. This lane does not touch `db.py`, synonyms, model selection, router behavior, or concurrent live edits.

## What people see

A short Portuguese add:

```
Pronto, anotei na lista de *Costco*:
🛒 Leite
🛒 Ovos
🛒 Arroz
```

A short Portuguese list:

```
Aqui está sua lista de *Costco* 🛒

🥛 Leite — 2 l (sem lactose)
🥫 Arroz
```

A preference change:

```
Combinado, salvei suas preferências:
💬 Vou responder em português.
🛒 Sua loja de costume: Costco.
```

An unresolved purchase:

```
Qual destes você quis dizer com *Paper towels*? Ainda não marquei esse item.
🔹 Paper towels (Bounty)
🔹 Paper towels (Generic)
```

English has equivalent conversational replies. Item names retain their existing stored wording, even when the reply is in the other language.

## Presentation decisions

- Add, update, buy, return-to-list, and completed removal confirmations name the actual items for batches of up to six. Larger confirmations state the count. An explicit list still shows every item.
- Lists use emoji bullets and preserve quantities, units, notes, purchased/needed distinction, and store aisle order. Section headers appear for lists longer than six items; tiny lists avoid many one-item headings.
- Routine store fallback explanations remain in `assumptions` metadata. They are not appended to replies when the selected store is already named. Do not restore parentheticals such as “used Shop, the last store touched.”
- Consequential item decisions remain visible: partial-name matches, unit selection, and buying an unlisted item receive natural explanations. Ambiguities retain all candidates and explicitly say which item has not been marked. A partially successful batch still confirms the items actually changed.
- Preference replies use human language names and labeled emoji lines. Saving one preference confirms the supplied fields instead of replaying a semicolon-separated record. Reading preferences does not create a person row.
- The automatic last-touched stamp is omitted from routine lists. Attribution and time details remain available through activity/history. Archive replies retain requester-local dates, public closer names, quantities, notes, product URLs, and outcomes; only archive item bullet styling changes.
- Help and onboarding use short conversational examples. Onboarding uses stored preferences and asks only for missing store/language choices. They do not claim image/video recognition or fetching capabilities that may be unavailable.
- Removal previews always enumerate every affected item, even when the batch is large. They retain the exact `/remover CODE` command and five-minute deadline. Formatting never stands in for confirmation.

## State and authority invariants

No mutation function was changed. Actor/database derivation, membership checks, private binding, snapshot comparisons, native confirmation authority, token consumption, and retry receipt transactions are unchanged. Presentation helpers perform no reads/writes and cannot execute a removal.

`reply` remains a complete answer. `assumptions` and `candidates` remain structured context, not text to append mechanically. The model may add natural connective wording under the root-owned skill policy, but must preserve item facts, uncertainty, and literal confirmation commands. Existing cached retry receipts may return an older reply for that same call until expiry; new calls receive the refreshed presentation.

## Validation

Final full core suite: **400 tests passed** (58.5 seconds). Focused API suite: **27 tests passed**. Bytecode compilation and `git diff --check` also passed. The six added checks cover:

1. Short Portuguese batches name items with emoji bullets.
2. Long confirmations stay brief while explicit lists retain every item.
3. List quantities, notes, and purchased/needed filtering survive.
4. Mixed successful/ambiguous purchase replies match actual database state.
5. Consequential partial-name assumptions remain explicit and natural.
6. Onboarding asks only for preferences that are missing, and asks nothing when both are saved.

Existing tests continue to cover household/private isolation, strict membership, native-only confirmation, token actor/expiry/replay/snapshot safety, atomic retries, preferences, and archive details including product URLs. Three old exact-wording assertions were updated to the new approved conversational contract; no safety assertion was relaxed.

Run from repository root:

```
PYTHONPATH=tools/grocery-list/core python3 -m unittest discover -s tools/grocery-list/core/tests -p test_agent_api.py -v
PYTHONPATH=tools/grocery-list/core python3 -m unittest discover -s tools/grocery-list/core/tests -v
```

Deployment belongs to the root integration lane. These edits were prepared and tested in the isolated worktree; this document alone does not claim a live rollout.

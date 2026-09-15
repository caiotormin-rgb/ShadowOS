# Plain-word mode switches

The user asked to remove the slash requirement for choosing Grocery or Doctor.
The new entrypoint handles a standalone mode word in native `before_dispatch`,
then sends a short acknowledgement without calling a model. Existing slash
aliases remain available.

**Status:** deployed and healthy on 2026-09-14. Runtime source: `165251db`.

## User behavior

| Standalone message | Mode | Acknowledgement language |
|---|---|---|
| `lista` | Grocery | Portuguese |
| `groceries` | Grocery | English |
| `médico` | Doctor | Portuguese |
| `medico` | Doctor | Portuguese |
| `doctor` | Doctor | English |

Use the existing brief, conversational acknowledgements, such as
“🛒 Lista ativa. O que vamos comprar?” and
“🩺 Busca de médicos ativa. Como posso ajudar?”. Do not append an English
translation to a Portuguese acknowledgement or advertise the other mode.
The alias chooses the acknowledgement language; it does not update the person's
saved Grocery language preference.

`/lista`, `/groceries`, `/medico` and `/doctor` remain compatible aliases.
A person does not need to learn or use slash syntax to select a mode.

## Exact matching and boundaries

The native matcher trims surrounding whitespace, normalizes Unicode to NFC,
and compares case-insensitively. It matches the **whole message**, not a keyword
inside a longer request. For example, ` LISTA ` selects Grocery, and an equivalent
Unicode spelling of `médico` selects Doctor.

These messages do not trigger an automatic switch:

- “mostra minha lista”
- “preciso de um médico”
- “doctor please”
- “add milk to groceries”
- “lista.” or “lista e médico”

Ordinary requests continue in the selected mode. If a request belongs to the
other domain, the assistant can briefly invite the person to send its standalone
mode word. The model does not infer a switch, simulate the acknowledgement, or
ask the person to select an already active mode again.

Matching still requires a trusted direct WhatsApp conversation, an allowed
account and sender, and the correct host-resolved household session agent.
Message text cannot supply or override those identities. Native mode selection
must fail closed when the required host context is missing or inconsistent.
No owner/admin capability is granted by a mode word. Canonical tool execution
continues through the existing domain and Access policies.

## Task and confirmation state

Selection updates the existing durable mode store. Acknowledgement alone does
not start a Doctor request, edit groceries, reset a transcript, or clear an
unfinished task. Short followups stay with the active domain. On returning to an
older task, inspect its state and clarify if a short response is ambiguous.

The destructive/outbound approval flows are unchanged: removal still requires
`/remover CODE` (with `private` when supplied), and approved Doctor email sending
still requires `/ok CODE`. Plain mode words never confirm either operation.
Those native commands retain their own identity, access-grant and token checks.

## Limitations

This is deliberate exact-word selection, not automatic sentence classification.
It adds no model call for switching and no broad fuzzy matching. A standalone
word may also be intended as a topic or product name; under this convention, the
whole-message alias selects its mode. Put an item in an ordinary request such
as “adiciona …” when that is the intended action.

The hook receives native command text chosen from `BodyForCommands`, then
`RawBody`, then `Body`. It does not expose media or input-provenance flags, so
this implementation cannot establish that a matching word was typed rather
than transcribed. Trusted channel identity checks do not establish text origin.

The current design still shares a person's own Grocery/Doctor session history;
mode selection is not separate transcript isolation. It does not widen the
model-visible or executable permissions. Callback tests alone do not establish
that a real channel turn is intercepted or that no model call occurred.

## Verification

`tools/household-router/src/router.mjs` registers `before_dispatch`. The handler
returns `{handled: true, text}` only after its state write. The installed host's
`dispatch-from-config-CmAXENud.mjs` awaits this hook, sends its reply and returns
before agent dispatch (lines 741–803). The native WhatsApp producer populates
`OriginatingTo` with the sender's direct conversation id, but the later claim
projection prefers `To`, which is the bot recipient. The installed WhatsApp
plugin has no `resolveInboundConversation` override. An actual mapper test
caught this before gateway activation. The handler therefore validates the
canonical `agent:<agent>:whatsapp:[<account>:]direct:<phone>` session key against
the trusted sender/account. It never treats `conversationId` as the principal;
other session kinds, peer mismatches and thread suffixes fail closed.

The complete routing/context/integration/native-dispatch suite passes 38 tests.
Ten native-dispatch cases exercise the actual installed mapper, claim projection
and hook runner, including the bot-recipient regression and following-turn
mode persistence. No model or delivery transport is started by this fixture. The three added mode
cases exercise all aliases, case/Unicode normalization, persistence/reopen,
revocation of old turns, following-turn tool access, nonmatching text and
mismatched/missing scope, and storage failure without success claims. The SDK
registration case also exercises the registered dispatch handler. Existing
native slash tests continue to pass. Config preparation/rendering passes its
8 existing tests and 3 new renderer tests. Confirmation implementations were
not changed. These are provider-free synthetic tests, not a delivered WhatsApp
conversation. No real user messages were sent by the tests.

## Deployment

Backup: `/home/openclaw/backups/plain-mode-switches-20260914T064742Z` contains the
previous live router and generated `AGENTS.md`. Its manifest records the live
config hash and the five independent pre-existing live-file changes so rollout
can check preservation. Router source requires no compilation; the canonical
base prompt is rendered to the shared workspace before gateway restart.

Rollout completed on 2026-09-14 from `165251db`, following `b6f1070c`.
The gateway restarted successfully; `openclaw health --json` returned `ok: true`
and both WhatsApp accounts were linked and connected. Runtime plugin inspection
listed `before_dispatch` alongside the existing model/prompt/end hooks and all
four native slash aliases. The live config hash and all five independent live
Grocery edits matched their pre-rollout hashes. No actual user message was sent;
a delivered channel conversation remains outside this controlled verification.

Rollback: restore the two backed-up files and restart the gateway as described
in [DEPLOYMENT.md](DEPLOYMENT.md). Existing slash aliases and mode database schema
are unchanged, so the previous router reads the persisted selections. Do not
reset the live worktree or overwrite its independent Grocery changes.

Canonical user guidance is `tools/household-config/prompts/BASE.md`. Avoid
hand-editing generated agent prompts. Root coordinates any other domain prompt
updates and live deployment so the instruction and dispatcher behavior agree.

The renderer uses explicit `MODE_ROUTING_START`/`MODE_ROUTING_END` markers in
BASE.md to replace mode guidance for the combined configuration. Keep those
markers when editing prose; combined mode must not advertise unavailable routing.

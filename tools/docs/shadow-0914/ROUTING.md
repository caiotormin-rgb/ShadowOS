# Explicit household modes — implementation and handoff

Status: enabled after explicit user approval of both conversation/prompt hook permissions. All four native commands and three typed hooks are registered; gateway health is good. See DEPLOYMENT.md for exact activation evidence and rollback. Router logic was tested with synthetic senders and installed-host callback composition, not live WhatsApp. No real-user messages were sent.

## User experience and deliberate scope

Use short, standalone commands:

- `/lista` or `/groceries`: shopping list.
- `/medico` or `/doctor`: doctor search.

Grocery is the initial mode, preserving the existing bot's main use. A command returns a short bilingual acknowledgement; the next message starts/continues the selected task. Commands with extra words are rejected with usage guidance. Plain `lista`, `médico`, quoted commands, and “expert” are **not deterministic switches**. Explain the slash commands in onboarding. No classifier and no inference from “yes” or “the second one.”

Switching changes one routing row, not the Grocery or Doctor database. Pending jobs and confirmations retain their own backend rules and expirations. `/remover CODE` and Doctor `/ok` remain independently authenticated native commands owned by those plugins; mode policy applies to model tool calls, not native commands. On returning to a domain, the prompt tells the model to inspect current task state and clarify stale short replies. There is no separate per-domain conversation history in this implementation.

## Why a small local plugin

Preflight inspected installed OpenClaw's native command registration, prompt/model hooks, and trusted-tool policies. Those existing primitives handle command dispatch, authenticated sender metadata, and tool execution. A small adapter supplies only mode persistence and domain policy; no alternate messaging transport, subagent orchestrator, or general framework was added.

The host at `/home/openclaw/.openclaw/tools/node-v24.19.0/lib/node_modules/openclaw` reports `2026.9.4`. Its bundled `docs/plugins/hooks/prompt-and-session.md` explicitly documents that Codex rejects restrictive `toolsAllow` because its tools belong to the thread. The old Grocery plugin's installed dependency reports `2026.7.1-2` and has older hook declarations missing `accountId` and provenance; validate the host rather than assuming the plugin dependency is current. Validate **the deployed host**, not just a package version.

## Files and interfaces

- `tools/household-router/src/router.mjs`: pure/configurable router, synchronous SQLite store, host registration adapter.
- `src/index.mjs`: native SDK entrypoint.
- `openclaw.plugin.json`: startup activation, strict schema, `household-mode` trusted policy declaration.
- `config.example.json`: plugin entry config using synthetic sender and placeholder account.
- `test/router.test.mjs`: Node built-in tests; no network/package install needed.

Required configuration: `statePath`, `agentIds`, `accountIds`, `allowedSenders`, `tools.groceries`, `tools.doctor`. Agent IDs may only be `shared-tools`, `shadow-shopper`, `shadow-care`; owner/main/dev IDs are rejected. Channel is hardcoded to WhatsApp. Sender/account allowlists are explicit pilot constraints, additional to host and tool access checks. Example list uses the seven narrow Grocery tools and `doctor_search`; never add legacy `grocery_list`.

Optional `defaultMode` is `groceries` unless specified. Optional `models` entries use `{ "provider": "...", "model": "..." }` for either domain. **Example omits models to inherit the configured Sol baseline.** No model escalation command exists. Model selection cannot alter allowed tools, agent IDs, grants, or sender identity. Model overrides are per-turn hook requests, not an assertion of effective model: verify the runtime's actual provider/model when benchmarking.

## Selected domain instructions

Optional `instructionPaths: {groceries: "/absolute/GROCERY.md", doctor: "/absolute/DOCTOR.md"}` loads only the selected domain text into the prompt. The example enables these paths; generate both files before activation, or omit the entire option to use generic routing guidance with existing workspace instructions. Use the shared configuration renderer's per-mode output. Keep the base workspace AGENTS instructions free of both full domain manuals if the purpose is reducing irrelevant prompt context.

Each selected file must be nonempty valid UTF-8, without NULs, at most 32 KiB. Reads are bounded even if the file changes during a read. Missing, oversized, empty, or invalid selected files invalidate the run snapshot and produce safe no-tool guidance. The tool policy rechecks file availability before executing a tool, so deleting the active file after prompt assembly also denies calls. An absent inactive file does not block the current domain. Content is trusted operator-owned configuration; do not point this option at user uploads. The plugin does not parse frontmatter; provide rendered instruction text.

This reduces newly submitted domain instructions. It does not remove old domain messages from an existing Codex conversation or narrow its submitted schemas. Start a fresh isolated pilot session to assess prompt size accurately.

## Guarantees and trust boundaries

1. State key is SHA-256 over `[channel, accountId, normalizedSender]`. Session resets/restarts preserve selected mode. Raw phone numbers are not stored in rows; hashes are pseudonymous identifiers, not encryption.
2. Native commands require host authorization, configured account/sender/agent, and direct-chat `from` matching sender. Group JIDs and malformed identities fail closed.
3. Agent hooks accept only supported sender/account/direct-chat metadata and reject explicitly inter-session/internal provenance. Missing provenance is tolerated **only for reading mode/prompt context**, because some human paths omit it; hooks never mutate state from user text. Call authorization separately requires host-proven `ctx.requester`.
4. Each run pins its initial mode and revision. Trusted-tool policy rechecks current revision on every call. Switching modes revokes an in-flight run; switching back cannot restore its old authority. A new run must begin.
5. Missing requester, missing run snapshot, mismatched sender/account, expired run snapshot (one hour), or state read failure blocks tool execution. Exact domain tool lists are deny-only: returning no denial cannot bypass another plugin's grant policy or the host tool ceiling.
6. The router never calls a domain tool, resets backend state, or invokes a subagent. Its only message-like output is a native command handler return for host delivery.
7. Store opens lazily on first real use, so plugin discovery does not write state. SQLite statements are synchronous and revision update is atomic; busy timeout is five seconds. File mode is `0600`, new directory `0700`. Existing parent directory permissions are not changed. State is small mode metadata, without task contents.

## Real limitations and required rollout verification

This is **one household agent with execution-time domain restrictions**, not fully separated Shopper/Care agents. Both tool schemas and previous conversation messages may remain in the same Codex thread. Prompt context cannot erase shared history or become a security boundary. Call-time policy is the domain execution boundary; static host guest tools and existing backend grant/requester checks remain required. A profile/model change is never an access grant.

Strict separation of submitted schemas or historical context requires separate statically restricted agents/sessions and a host-supported dispatcher that preserves trusted requester identity. Do not simulate that with unrestricted `sessions_spawn` or `sessions_send`. Dedicated Doctor research must run under an agent outside this router's configured conversational agent scope.

The mock tests prove adapter logic, not full runtime hook coverage. Before enabling for real guests, test an isolated host with the actual WhatsApp/Codex path: `accountId`, sender, direct `chatId`/`channelId`, stable `runId`, and `ctx.requester` must all be populated; trusted policy must run for submitted Codex tools. Missing fields intentionally block. Host chat-ID conventions can differ: this plugin currently accepts only phone-like direct IDs (including `whatsapp:` and `@s.whatsapp.net` forms). Do not loosen identity checks by parsing session keys or prompt text.

The initial safe rollout installed source and linked the entrypoint SDK while disabled. The subsequent approved activation enabled the router and was verified with runtime inspection. For future approved activation, put the example's contents under `plugins.entries.household-router`, replace synthetic account/sender, retain host guest tool allowlists, and restrict the initial pilot to the owner's test conversation. Runtime activation also needs the plugin load path/install entry. Validate effective config and rollback backup before restarting. Disabling/removing the plugin restores prior routing behavior; its private SQLite file can remain for re-enablement. Do not delete Grocery/Doctor databases as rollback.

## Tests and evidence

Run with the deployed Node 24 binary:

```sh
/home/openclaw/.openclaw/tools/node/bin/node --test tools/household-router/test/router.test.mjs
/home/openclaw/.openclaw/tools/node/bin/node --check tools/household-router/src/router.mjs
```

Eleven tests pass: initial Grocery; explicit mode persistence/reopen; sender/account isolation; no text-based switch; unauthorized/group/missing identities; trusted requester mismatch; cross-domain and shell/subagent denials; in-flight revocation; expert model permission invariance; storage failure; adapter registration; bounded phone normalization; selected instruction isolation and unavailable/oversized instruction revocation; disabled example pending hook approval. The adapter test uses mocks and sends nothing. No paid model call was made.

Next meaningful integration check: native `/medico` then ordinary short reply on a synthetic isolated WhatsApp-equivalent host, with policy denial probes for `grocery_add`, shell, and arbitrary inter-session requester claims. Separately verify `/lista` cannot mutate or cancel an existing Doctor request and that `/remover` still enforces its own expiration/snapshot checks.


## Additional integration evidence

Root verified the actual `src/index.mjs` SDK import against the deployed host; SDK links now exist in worktree and live source. `tools/household-config/test/integration.mjs` adds two passing checks using the real host lifecycle context builder, registered plugin callbacks, real AccessClient, and a scratch private Grocery database. This is stronger than the router unit adapter mock; it still does not exercise actual WhatsApp transport or a paid Codex turn. Final base instructions are 1,158 bytes and Doctor instructions 1,664 bytes; Grocery source skill is 4,728 bytes including frontmatter. The active combined fallback AGENTS is 7,419 bytes versus 11,840 bytes before rollout. Selected-mode prompting is now enabled after explicit user approval. These are file sizes, not total submitted-context/token/cost measurements.


## Deployment permission gate discovered during host review (resolved)

The installed host gates non-bundled conversation hooks independently from plugin manifest declarations. The exact proposed entry settings are `plugins.entries.household-router.hooks.allowConversationAccess = true` and `plugins.entries.household-router.hooks.allowPromptInjection = true`, followed by `plugins.entries.household-router.enabled = true`. They belong under the plugin **entry**, alongside `config`, not inside `config`. The first permission is mandatory for before_model_resolve, before_prompt_build and agent_end. Prompt injection defaults allowed, but an explicit false blocks before_prompt_build. No `contracts.hooks` manifest declaration is required. The existing `contracts.trustedToolPolicies: ["household-mode"]` is required and correct; trusted policies also require explicit entry enablement.

Automatic approval review rejected staging these hook grants as a security-boundary change requiring exact user approval. The bundle and example therefore keep the router disabled and do not stage an equivalent bypass. Approval is requested for this plugin's household conversation/prompt hooks, with router behavior restricted in code/config to `shared-tools`, the existing WhatsApp account, and the four already invited users. The host hook permission itself is plugin-wide: agent/account/sender checks inside the router enforce that narrower operating scope. The plugin does not change owner/dev capabilities or grant tools.

After explicit approval, apply those exact hook settings, enable the plugin, validate config and inspect its **runtime** registration. Without the grants, the host skips prompt/model/end hooks; enabling only the trusted policy would leave no pinned run snapshots and block all guest model tool calls. While disabled, neither mode commands nor domain routing are active; retain combined Grocery+Doctor instructions for ordinary operation.

Read-only source verification: `loader-runtime-load-DpX1CRjH.mjs` lines 4080–4095 apply prompt/conversation hook registration gates; lines 2339–2360 require the declared and explicitly enabled trusted policy. `setup-Dw2a6SKw.mjs` resolves before_model_resolve before model selection (skipped when modelSelectionLocked). The installed Codex `run-attempt-CvsAAGa0.js` builds hook context including runId, sender, account and provenance around line 3800, then calls the shared before_prompt_build helper around line 5040 before turn submission; restrictive toolsAllow throws. Native command registration has no additional conversation-hook permission gate; command authorization remains required on invocation.

Approval update: the user answered “yes” to the exact hook-permission request.
The gate described above is historical, not an outstanding blocker. The live
entry is enabled with both grants; its four commands and three hooks were
verified with plugins inspect --runtime, and gateway health includes the router
without errors. The source example remains disabled intentionally. Detailed
activation backup, config/prompt hashes and transport-test limits are in
DEPLOYMENT.md.

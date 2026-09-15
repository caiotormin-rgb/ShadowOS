# Deployment record — 2026-09-14

## Latest follow-up: Grocery loop and conversational replies

The subsequent owner report (/lista succeeded but list requests asked to switch
again) is corrected in source commit e973a109, including member renderer commit
ea303b8d. Canonical base/Grocery instructions were regenerated and the gateway
restarted. Router stays enabled; models, permissions, saved modes and all other
configuration are unchanged. Both WhatsApp accounts are connected, health is
true and no plugin errors are reported.

400 core tests passed in the worktree; 27 API tests and 19 router/host-context
tests also passed against the deployed checkout with the user's independent
DB/synonym edits present. Seven benchmark-grader and eight config-render tests
passed. The five independent live edits were hash-verified unchanged.

Immediate backup: /home/openclaw/backups/grocery-reply-repair-20260914T062351Z.
For rollback restore router.mjs, agent_api.py and the paired base/Grocery
instructions from that backup together, preserving unrelated source edits.
member_render.py can remain unused if the old API is restored. Restart the
gateway after restoring router source. Do not restore databases or erase modes.
See ROUTING-REGRESSION.md and GROCERY-REPLIES.md for exact changes and evidence
limits; this follow-up supersedes older verbatim-reply instructions.

Final native replay exposed blocked broker discovery after that first repair.
The complete broker correction is deployed at source commit cc8ef71a; Access
was rebuilt, three live callback integration tests passed, and gateway restart
finished healthy with both WhatsApp accounts connected. The owner’s persisted
mode remains groceries. Config and all five independent user-edit hashes are
unchanged.
Access and router now understand authenticated tool_search/tool_describe/tool_call
and enforce the underlying canonical target. The final correction adds 29 Access
tests and 24 composed router/host-context tests, all passing. Actual built-in Sol
replay passed with fresh backend reads, no tool failures and correct emoji list
output. Additional immediate rollback source/builds are in the backup's
before-broker-fix directory. Restore Access dist and router together if needed.
See ROUTING-REGRESSION.md for raw evidence and the legacy-history test limit.

## Original activation status

Safe rollout deployed; the user subsequently approved the exact router hook
permissions. Router activation and gateway restart are complete. Source implementation
commit 4fcfda17 was fast-forwarded from baseline a4280e29 into
/home/openclaw/.openclaw/workspace. Subsequent documentation-only commits record
this rollout. Work branch: codex/shadow-0914; isolated worktree retained at
/home/openclaw/.openclaw/worktrees/shadow-0914.

Active changes:

- Seven narrow Grocery tools for shared-tools, compact backend replies,
  requester-private lists, retry receipts, native /remover confirmation.
- Access enforcement for shared-tools; native Grocery/Doctor confirmations
  recheck current grants through the compiled AccessClient.
- Doctor research explicitly defaults to Sol independently of chat selection.
- ShadowDev configured with Sol, coding profile, separate workspace, no public
  channel binding; stateless model readiness returned READY.
- Common AGENTS plus active-domain Grocery/Doctor instructions, generated from
  canonical sources.
- Router enabled with approved allowConversationAccess and allowPromptInjection
  hooks; /lista, /groceries, /medico and /doctor registered. Default is Grocery.

Existing owner main configuration, all channel settings, WhatsApp allowlists,
bindings, and shared-tools model were compared with backup and preserved exactly
as data. No database restore, invite/grant mutation, family message, Doctor
search or practice email was performed by the rollout.

## Evidence

520 offline tests passed (VALIDATION.md). Access built first, followed by Grocery
and Doctor. Installed 2026.9.4 config validate accepted both private candidate and
live config. Only warning: disabled router has staged configuration.

Gateway systemd service is active/running after restart. OpenClaw health reports
ok=true, hot reload active, no plugin errors/unavailable plugins; Access,
Grocery and Doctor loaded. WhatsApp default and tools accounts both report
configured/linked/running/connected with lastError=null; Telegram also connected.
The two synthetic installed-host callback tests passed against deployed files.
These are not proof of an actual inbound WhatsApp interaction or media behavior.

Private bundle:
/home/openclaw/.openclaw/evaluations/shadow-0914/deployment-safe.
manifest.json records baseline hash, intended files and hashes; applied.json
records the result. Exact staged file hashes matched live after application.
config.patch.json is an RFC6902 review diff, never input to config patch. Full
candidate was validated with OPENCLAW_CONFIG_PATH and OPENCLAW_CONFIG_READONLY=1,
then applied by private atomic file replacement after baseline-hash comparison.
Generated instructions were installed first and config last, then service restart.
The full candidate contains existing secrets: do not commit, print or share it.

## Approved router activation — 05:12 UTC onward

Automatic approval review initially rejected the hook grants. The user explicitly
approved them with “yes” after the exact permission and scope explanation.
Approval is resolved; do not ask again for this same activation.

The active entry has hooks.allowConversationAccess=true,
hooks.allowPromptInjection=true and enabled=true. Host hook access is
plugin-wide; router checks restrict processing to shared-tools, the existing
tools WhatsApp account and its four allowed senders. No model/tool/account
allowlist, binding, owner or development setting changed during activation.
A structural comparison verified only these router flags changed.

Private activation bundle and immediate rollback backup:
/home/openclaw/.openclaw/evaluations/shadow-0914/router-enable-20260914T051200Z.
It contains config.before.json, AGENTS.before.md, exact candidate files and
manifest.json with original/final hashes, approval and verification record.
Never commit or print config copies: they contain existing secrets.

Candidate and live config validation passed without warnings. Runtime inspection
confirmed four commands, three typed hooks (before_model_resolve,
before_prompt_build, agent_end), household-mode policy declaration, service,
explicit activation and both approved permissions; no diagnostics. Thirteen
focused router/installed-host callback tests passed. After restart, the first
health probe raced startup; the next returned healthy=true, router loaded,
no plugin errors, hot reload active and both WhatsApp accounts connected.
Live config and base AGENTS hashes match the validated candidates.

An actual inbound WhatsApp transition has not been exercised. No real messages
were sent to test activation. The user can send /lista or /medico as a standalone
message; their own selected mode then persists. Existing pending tasks retain
backend state, authorization and confirmation-expiration rules. Sol remains
unchanged for both domains.

For routing-only rollback, preserve any newer config edits, restore the router
entry from config.before.json and combined AGENTS.before.md together, validate
and restart. Do not restore the entire old config over unrelated newer changes.
Do not remove Grocery or Doctor state. The preparation script/example remain
disabled by default as safe scaffolds and refuse to overwrite this enabled
entry; they are not an idempotent updater for the activated deployment.

## Backup and rollback

/home/openclaw/backups/2026-09-14-shadow-refactor contains original config
(mode 0600), shared workspace, compiled plugin artifacts/manifests, consistent
Grocery SQLite backup and original-source/config-hash manifest. SQLite backup
integrity was checked before source/config changes.

For rollback, first compare current state and save any post-rollout edits.
Restore backed-up config and shared instructions plus prior plugin builds as a
coordinated set, validate config and restart the existing gateway service.
Reverse the source implementation commits if needed without discarding later
user changes. Keep Doctor's explicit Sol pin if research quality must remain
independent of the chat model. ShadowDev and disabled-router files can remain
unbound/unloaded when their config entries are removed.

New API tables are additive. Do not restore an older Grocery database over
newer household activity merely to roll back code. If database recovery is
needed, reconcile post-backup mutations first. Disabling the router preserves
its small mode SQLite file; never delete Grocery/Doctor databases for routing
rollback.

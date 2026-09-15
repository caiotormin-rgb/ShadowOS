# Independent integration review — 2026-09-14

Reviewed the new Grocery API and bridge, router policy/commands, Access grant
checks, Doctor native confirmation and research-model separation. Review used
source reads and provider-free synthetic tests. No live config changes, model
calls, emails, WhatsApp sends or production database mutations occurred.

## Concrete findings and disposition

### Fixed: native confirmations lacked direct-conversation restriction

Router mode commands already required a personal native `from` matching the
authenticated sender. Grocery `/remover` and Doctor `/ok` lacked that check,
and bypassed router/model policies. An allowlisted sender on the right account
could therefore attempt a private confirmation from a group. Doctor's response
also included recipient contact information in that conversation.

Both handlers now require a strict personal `from` identity matching the same
authenticated sender, exact account and host authorization. Missing or group
`from`, mismatched sender and arbitrary text containing phone digits refuse
before engine execution. This is defense in depth even if the current channel
configuration rejects groups before plugin command dispatch.

### Fixed: Doctor sender fallback and permissive command parsing

Doctor used digit stripping, accepted `from` when senderId was missing, and
accepted a valid `/ok CODE` followed by arbitrary words. It now accepts bounded
transport forms (E.164/digits, optional WhatsApp prefix/personal JID), requires
senderId independently, and requires the entire argument string to be exactly
six hexadecimal characters. Genuine personal JIDs remain accepted.

### Verified: command grant checks execute against the real shared client

Both commands were tested with a temporary Access schema and dynamically loaded
production AccessClient. An active grant permits the mocked execution; revoking
the grant or removing the person denies the next call. Owner role does not
bypass either command's required grant. Missing store/module denies. Optional
accessDbPath omission preserves legacy allowlist behavior, so pilot deployment
must supply it.

### Verified: new API/tool mapping and private behavior

Core fixed the integration mismatch between `changeType` and its activity action
filter and made no-field preferences a read-only response. Narrow preferences
supports walkOrder through the shared API. Private requests use only actor-hashed
paths and the trusted private flag. API binding refuses a different private
owner; family membership is required. Removal previews do not delete anything.
The native confirmation is requester scoped and cannot be invoked by any
registered model schema. Same-call receipts do not imply inbound-message dedupe.

### Runtime compatibility finding

The actual host is
`/home/openclaw/.openclaw/tools/node-v24.19.0/lib/node_modules/openclaw`, package
version **2026.9.4**. The reused Grocery build dependency at
`/home/openclaw/.openclaw/workspace/tools/grocery-list/plugin/node_modules/openclaw`
is **2026.7.1-2** and has older context declarations. Inspecting that older
bundle initially suggested accountId was absent from hooks; this is not true
of the actual host. Do not use the build dependency as runtime evidence.

Actual `lifecycle-hook-helpers-Bnm4Rpmf.mjs` exports buildAgentHookContext and
preserves runId, accountId, inputProvenance, sender and chat identity.
`hook-agent-context-Dt0tDMDi.mjs` supplies channel/chat fields. Actual
`agent-harness-runtime-B5W8bB-V.mjs` uses that builder for before_prompt_build.
The trusted policy evaluator accepts both `{allow:false,reason}` (Access) and
`{block:true,blockReason}` (router); these compose as denials, not grants.

## Executable integration smoke

Run after building Grocery, Doctor and Access:

```bash
node --test tools/household-config/test/integration.mjs
```

Two tests pass using the actual installed host's context builder, registered
production plugin callbacks and real temporary SQLite databases:

1. Build authenticated host context; combine router and access policies; allow
   Grocery and refuse Doctor/shell in Grocery mode; private add → removal
   preview → group refusal → grant revocation refusal → restore grant → native
   confirmation → verify the item is absent.
2. Native Doctor mode switch invalidates the previous run. A fresh host context
   permits only Doctor-domain tools and blocks Grocery.

The test discovers the host's bundled context helper dynamically and checks its
export. `HOUSEHOLD_TEST_OPENCLAW_ROOT` may override the installation path. This
is intentionally version-specific: a changed host helper should prompt review.
The test is a callback-composition test using real production implementations;
it does not register plugins in a running gateway or emulate WhatsApp transport.

Additional validation: Grocery TypeScript + **31 plugin tests**, Doctor
TypeScript + **10 plugin tests**. Both include mocked engine execution for native
command trust/grant cases. Core and router full-suite results are documented by
their respective lanes.

## Remaining checks before production expansion

- Verify native command `from`, account and sender fields from the real WhatsApp
  transport in a controlled owner-only pilot. Missing identity fails closed.
- Confirm all seven narrow tools and doctor_search receive trusted policy checks
  in the chosen model/runtime. Callback smoke is not a full harness proof.
- Keep the union of tools available only within a restricted agent ceiling;
  mode policy enforces use, but does not reduce model-visible schemas on every
  runtime. Benchmark metadata must describe the actual schema exposure.
- Newer compiled host modules change compatibility independently of npm package
  versions. Record executable path and effective runtime along with model.
### Fixed after review: Doctor operational diagnostics exposed to model

The pre-existing unexpected subprocess failure path threw raw stderr/message.
It now returns a fixed `{ok:false,error:"unavailable",message:...}` envelope.
Known domain JSON refusals and timeout codes remain inspectable. New mock tests
cover subprocess stderr containing private paths and malformed stdout; neither
is returned to the caller. No provider calls were required.

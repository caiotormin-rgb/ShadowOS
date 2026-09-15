# Actual built-in runner regression probe — reproduced and fixed

**Two actual native runs:** initial discovery-wrapper denial, then successful
post-fix discovery and current-list reads. **The supplied legacy tool pair was
sanitized by the host (`replayInvalid: true`); this is not proof that a model
consumed or recovered from the stale pair unchanged.**

`tools/household-config/test/native-replay.mjs` is an explicit opt-in diagnostic
for the installed OpenClaw built-in runtime. It is separate from the passing
callback-composition tests. Without `--run`, it only prints its isolation and
budget settings. `--run --turns 1|2` permits at most two native runs, each capped
at 120 seconds with an abort signal. Do not invoke it casually as an offline test.

The attempted scenarios are a fresh synthetic conversation asking “Show me the
current lists”, followed by a separate detached conversation whose synthetic
history mentions retired grocery_list. The second scenario is skipped if the
first runner fails before completion. `--legacy-history --turns 1` selects only
the legacy-input scenario for a bounded follow-up run.

Isolation:
- A new `/tmp/grocery-contract-native-*` root contains config, workspace, prompts,
  Grocery family/private DBs, Access grants, router mode DB, runtime state and logs.
- Explicit synthetic sender/account and only the seven production Grocery
  capabilities in the agent allowlist. Doctor is disabled. Shell, filesystem,
  inter-session and message tools are denied; disableMessageTool is set.
- WhatsApp plugin metadata is registered with a disabled synthetic account;
  channel services are never started and no delivery callback exists.
- Actual stored model auth is read from the existing shared-tools agentDir;
  auth tokens are never copied. authProfileStateMode is read-only, auth failure
  policy local. Normal prepared runtime is required for tool-capable runs; the
  read-only preparation mode is limited to model probes and drops plugin tools. SessionManager is in memory, sessionPersistence detached,
  and trajectory persistence disabled.
- Actual prepareSystemAgentRunAdmission creates the host run token; no manually
  forged admission object or permission bypass is used.

## Actual provider result

After using the supported prepareSystemAgentRunAdmission and normal prepared
runtime, the actual built-in OpenClaw harness called `openai/gpt-5.6-sol` against
the synthetic fixture. The model received exactly the native deferred controls
`tool_search`, `tool_describe`, `tool_call`; the catalog held the seven production
Grocery capabilities. It attempted `tool_search` once and the Access trusted
policy denied that wrapper as an unmapped tool, before discovery could resolve
the underlying capability.

Actual reply:

> Sorry, I can’t access your grocery lists right now because the list service
> isn’t available for this account.

Elapsed: 6,208 ms. Tool calls: 1. Failed calls: 1. Successful underlying tools: 0.
No Grocery mutation or delivery occurred. This demonstrates a real native
runtime regression that callback-only tests had missed: discovery controls
traverse policy before the canonical inner tool. Root was notified that Access
and router must support the host discovery controls while retaining canonical
inner-call checks.

Artifacts: `/tmp/grocery-contract-native-L98ngz` contains synthetic config,
loader results, metadata/source hashes, `run-0.json` with complete tool outcomes
and model report, and runtime logs. Existing auth was used read-only; no tokens
were copied. This was the first actual provider run of the two-run budget.
No real WhatsApp transport or send was involved.

## Post-fix native replay passed

After root Access/router changes admitted the host's discovery controls while
checking qualified targets and canonical inner calls, the one remaining actual
provider run succeeded:

```bash
node tools/household-config/test/native-replay.mjs --run --legacy-history --turns 1
```

Actual built-in `openai/gpt-5.6-sol` execution took **10,216 ms**. It used
`tool_search`, followed by two `tool_call` operations targeting
`openclaw:grocery-list-tool:grocery_show`: first stores, then the Shop list.
The host reported five tool events including inner/outer observations, **zero
failures**, and successful names `tool_search`, `grocery_show`, `tool_call`.
No `tool_describe` call was necessary because search returned the usable schema.

Actual reply:

> Here’s your current Shop list 🛒
>
> 🍞 Bread
> 🥛 Milk — 2

Six checks passed: fresh successful backend receipt, unchanged fixture state,
no foreign sentinel disclosure, real tool calls, zero failures, and fixture
store shown. Artifacts are `/tmp/grocery-contract-native-W2U1Sq`, especially
`run-0.json` and `metadata.json`. Exactly **two actual model-run invocations**
were performed in this task: the initial wrapper denial and this successful
post-fix replay; each native run may make several model generations while using
tools. No more provider runs were performed after this success.

The synthetic history contained a legacy `grocery_list` assistant toolCall and
toolResult. The host reported `replayInvalid: true`, indicating replay repair or
sanitization. This proves successful discovery/current reads with legacy input
supplied to the native runner; it does **not** prove the model saw that old pair
unchanged or independently recovered from a retired tool call. Actual user
history and WhatsApp transport remain outside this test. It is also only a
single successful regression sample, not a model-quality benchmark.

The reusable script exits nonzero for runner failures or failed semantic/state
checks. It is opt-in and excluded from ordinary offline suite invocations.

## Setup findings

The initial call lacked a host-created run admission token and failed before
provider dispatch. Adding the supported prepareSystemAgentRunAdmission fixed
that setup error. `preparedModelRuntimeMode: isolated-read-only` then caused
all registered tools to disappear in built-in tool construction, despite the
standalone actual resolver finding all seven. Removing that inappropriate
model-probe-only preparation mode while preserving authProfileStateMode:
read-only enabled the native tool-capable run. These pre-provider setup failures
did not consume provider calls. The final script omits the redundant per-run
toolsAllow and retains the exact agent capability allowlist, allowing the host
to build its deferred control surface.

Root retained metadata, loaded registry, run result and synthetic config at
/home/openclaw/.openclaw/evaluations/shadow-0914/native-before and native-after
for durable handoff. These copies contain synthetic fixtures, not real member
conversations or copied auth credentials.

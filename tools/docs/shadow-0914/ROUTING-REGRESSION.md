# Grocery conversation regression — 2026-09-14

## Report and evidence

The owner sent /lista, received a successful acknowledgement, then asked
“Show me current lists”. The assistant told them to send /lista again. A following
Portuguese list question repeated the loop. The owner also requested much more
conversational replies with emoji bullets.

Read-only inspection of that owner's session and mode store confirmed Grocery
was selected before the failed requests. The assistant attempted the retired
grocery_list tool for three stores. All three calls were denied by the router's
single generic message: use /lista or /medico. That text incorrectly portrayed
an unavailable-tool call as a missing mode selection, and subsequent replies
followed the false explanation. No Grocery operation executed in those denied
calls. No other member conversations were inspected for this investigation.

The actual session uses the built-in openclaw harness and deferred tool_search,
tool_describe and tool_call. Earlier composition tests used a generic lifecycle
helper and fresh tool names. Those tests did not exercise stale tool descriptions
from a pre-migration conversation through the actual model/broker path. The
previous healthy service/registration checks were insufficient to prove that UX.

## Additional root cause reproduced in the actual runtime

The first actual built-in Sol replay reached tool_search and was denied by
Access as an unmapped tool. The model then said list access was unavailable.
This exposed a second, independent bug: both custom policies had modeled only
canonical domain tools, while the real harness also authorizes its deferred
broker wrappers. A fresh request could fail before discovering a current tool.

Access now permits metadata discovery only after authenticating a person with
an existing household grant. For tool_describe/tool_call it decodes only exact
fully qualified household capability IDs and checks that target's grant. The
router retains all requester/run/revision/instruction checks before discovery;
description/call decode the exact ID and apply the same active-domain ceiling.
Foreign namespaces, wrong plugin owners, malformed/bare/newline IDs, shell and
other domains remain denied. The host separately evaluates the wrapped
canonical tool again. This is not an unconditional tool_call bypass.

The post-fix native Sol replay succeeded: tool_search followed by qualified
grocery_show stores/list, two real backend reads, zero tool failures, correct
Bread/Milk quantities and emoji bullets, unchanged synthetic household state,
and no foreign sentinel disclosure. Fixture receipt rows prove the answer used
fresh backend results. Artifacts: /tmp/grocery-contract-native-W2U1Sq. Initial
wrapper-denial artifact: /tmp/grocery-contract-native-L98ngz.

Exactly two provider runs were used. Earlier helper setup failures occurred
before any provider call. The supplied synthetic legacy toolCall/toolResult was
sanitized by the host (replayInvalid=true); therefore this proves the actual
broker/read path, not unchanged legacy-history consumption. Separate policy
regressions prove safe recovery from a rejected legacy call. No real WhatsApp
message or household record was used by the probe.

## Correction

- Current mode instructions explicitly override old mode errors in history.
  When Grocery is active, proceed with Grocery requests without another switch.
- Current narrow tool names are listed in active context. Rediscover them rather
  than reusing historical grocery_list descriptions. All/current lists first
  fetch the available stores, then their current contents.
- Retired grocery_list remains denied. After current requester/run/revision and
  instruction-file checks, its denial explains that Grocery is already active
  and directs the model to grocery_show/current tools. It grants no extra access.
- Identity failures, in-flight mode changes, unsupported capabilities and actual
  cross-domain requests receive different recovery guidance. Only a real
  cross-domain request asks for the corresponding switch. Runtime failures do
  not tell the user to repeat an already-successful command indefinitely.
- Native acknowledgements are short, friendly and use the alias language plus
  one emoji. /lista is Portuguese; /groceries is English. No bilingual paragraph
  or unsolicited pitch for the other mode.
- Grocery member renderers and canonical instructions now support conversational
  wording and emoji item/choice bullets. See GROCERY-REPLIES.md. Facts, partial
  failures, matching uncertainty and exact confirmation commands remain required.

No transcript reset, user mode reset, model downgrade, permission expansion or
live list mutation is part of this correction. Existing in-flight invalidation,
owner/guest separation and destructive confirmation checks are retained.

## Evaluation changes

Verbatim relay is no longer a product requirement. The direct API benchmark
retains relayExact as a diagnostic and marks changed wording as pending semantic/
language review, not automatic success or an automatic wording defect. State,
privacy, tool validity and authorization failures remain hard failures. Historical
14-case Gemini results used the previous stricter prompt/grader and should not
be represented as validation of this new conversational policy.

## Tests and deployment

New builtin-context.mjs tests use the actual installed channel/identity builders
with realistic messageChannel, agentAccountId, sender/chat and run metadata.
They cover /lista then EN/PT list requests, current tool success, stale tool
recovery, native identity prefixes and unauthorized/inter-session failures.
The original router/callback tests retain switch-revision denial and privilege
checks. The full focused set has 19 passing tests. The core suite has 400 passing
tests, with 27 API tests repeated against live source after integration. Seven
benchmark tests and eight config-render tests pass.

Deployed source e973a109 plus regenerated base/Grocery instructions; gateway
restarted healthy, both WhatsApp accounts connected, no plugin errors. Existing
router enablement, permissions, models and saved Grocery mode are unchanged.
Backup: /home/openclaw/backups/grocery-reply-repair-20260914T062351Z. All five
concurrent user-edit hashes matched after deployment.

Actual native replay results are now resolved as described above. The new
focused totals are 29 Access tests and 24 router/host-context integration tests.
The current user-facing inbound WhatsApp flow after this final correction has
not yet been observed; native replay uses trusted synthetic transport metadata.


The live checkout contains independent user edits in Grocery DB/synonym/tests.
Deploy only this branch's named paths, preserve those edits, and verify hashes.
No work on those unrelated edits is part of this incident.

Final deployment: cc8ef71a, Access rebuilt and gateway reloaded. Three callback
integration checks passed on live source/builds. Health reports both WhatsApp
accounts connected and no plugin errors; owner mode still groceries. Config and
independent user edits match their protected hashes. Durable synthetic evidence
copies: /home/openclaw/.openclaw/evaluations/shadow-0914/native-before and
native-after (metadata, registry, run result and synthetic config only).

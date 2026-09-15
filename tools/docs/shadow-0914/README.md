# Shadow / Grocery — September 14 implementation

Start here. This directory is the handoff for the authorized parallel project.
Latest follow-up: PLAIN-MODE-SWITCHES.md documents standalone lista/groceries and
médico/medico/doctor mode selection without a required slash. The prior
ROUTING-REGRESSION.md explains the /lista loop, and GROCERY-REPLIES.md documents
the conversational emoji replies.
Production status and exact rollback material are in DEPLOYMENT.md; test results
and benchmark qualifications are in VALIDATION.md. Do not infer deployment from
source presence or a green mock test.

## Decisions

1. Keep one family WhatsApp contact. Switch with the standalone word lista or
   groceries for shopping, or médico, medico or doctor for provider search.
   Existing slash aliases still work. An exact native before_dispatch handler
   acknowledges the switch without a classification/model call. A sentence
   containing one of these words does not switch automatically. Followups stay
   in the selected mode; switching does not reset requests or approve pending
   /remover or /ok confirmations. Check PLAIN-MODE-SWITCHES.md for verification
   and deployment status of this latest entrypoint.
2. Separate permission scopes from model tiers. Model strength is configuration;
   expert mode never means access to owner files or admin tools.
3. The approved routing design uses the existing restricted shared-tools agent
   with a domain execution policy and active-domain instructions. The initial safe
   rollout used combined instructions; the user subsequently approved both hook
   permissions and domain routing is now enabled. Codex cannot narrow dynamic
   tool schemas per turn. Therefore this is mode routing, not separate transcript
   isolation: a person's own Grocery and Doctor history still share a session.
   Different senders remain isolated by host dmScope and backend ownership.
4. Keep Sol until comparative workflow evidence supports another model. Doctor
   research explicitly pins Sol independently of the chat model. Configure a
   separate owner-only development agent without a public channel binding.
5. Keep Python/SQLite domain logic. Add a small member API with ready-to-send
   output, consistent narrow tools and native delete confirmation. Preserve the
   legacy operator CLI. No arbitrary web/shell capability is added for URLs.

## Implementation map

- GROCERY-API.md: backend transaction, compact output, privacy, preferences,
  receipt identity, deletion token lifecycle and backend compatibility.
- GROCERY-PLUGIN.md: narrow tool schemas, bridge, native command checks,
  manifest/build behavior and limitations.
- PLAIN-MODE-SWITCHES.md: standalone-word dispatch, slash compatibility,
  exact matching, verification and deployment status.
- ROUTING.md: authenticated commands, durable modes, hook/runtime contract,
  in-flight invalidation, model selection and context limits.
- ACCESS.md: opt-in guest enforcement, grant aliases and native command gates.
- DOCTOR.md: explicit research model and approval boundaries.
- REVIEW.md: independent integration review and resolved/open findings.
- VALIDATION.md: exact commands, results and evidence scope.
- MODEL-EVALUATION.md: Gemini pilot, manual review and promotion criteria.
- DEPLOYMENT.md: live state, configuration, backups and rollback.
- PLAN.md: initial ownership and coordinated work plan.

Canonical prompts live under tools/household-config/prompts and
 tools/grocery-list/skill/SKILL.md. Render with tools/household-config/render.py;
do not hand-edit the generated household workspace. The approved, enabled router loads the active domain's generated instructions;
workspace AGENTS contains common rules. The combined renderer is retained for
routing-disabled fallback. See DEPLOYMENT.md for activation and rollback. Agent configuration templates preserve the existing owner model catalog.

## Model evaluation policy

Evaluate each workload and actual runtime, not model name alone. Record final
state correctness, PT/EN behavior, privacy/authority violations, retries,
median/p95 latency, prompt/schema sizes and provider usage. Keep authorization
and destructive-action failures separate: they are hard failures, not an
average accuracy allowance. Use synthetic scratch databases and no outbound
messages. Direct API results are useful contract evidence but not a substitute
for the restricted OpenClaw/WhatsApp path or media tests.

Automatic provider fallback handles availability errors, not poor-quality
answers. Never replay a mutation blindly after an uncertain timeout. Receipts
protect repeat host tool-call IDs, not a semantically identical new message.

## Successor checklist

Read DEPLOYMENT and VALIDATION first. Check git status and current config before
editing. Never copy production OAuth tokens into isolated benchmark homes.
Do not run OpenClaw CLI startup probes concurrently: its state-lifecycle lock
can reject one while another starts. Existing source worktree and public ZIP
gazetteer dependencies are described in validation. Build access before sibling
plugins because native approval checks reuse its compiled AccessClient.

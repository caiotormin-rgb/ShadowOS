# Shadow and Grocery implementation — 2026-09-14

Status: implementation in isolated worktree; production unchanged.

User authorized implementation, parallel agent work, focused tests, and thorough documentation.

## Goals
- Simplify Grocery model input/output while retaining deterministic household behavior.
- Separate Doctor research model from the household conversation model.
- Support explicit WhatsApp modes: lista/groceries and médico/doctor, preserving sender and task state.
- Keep guest capabilities separate from owner development/personal capabilities.
- Evaluate before choosing a lighter production model.

## Ownership
- grocery_core agent: tools/grocery-list/core/agent_api.py, supporting new core files, focused tests, docs/shadow-0914/GROCERY-API.md.
- grocery_plugin agent: tools/grocery-list/plugin/** and tools/grocery-list/skill/**, docs/shadow-0914/GROCERY-PLUGIN.md.
- routing agent: new tools/household-router/**, docs/shadow-0914/ROUTING.md.
- root: Doctor model decoupling, access integration, deployment templates, evaluations, overall documentation and integration.

## Shared contract
Backend agent_api.py accepts --db PATH --actor PHONE --request-json JSON.
Request fields retain existing action semantics. Model never supplies actor or database path.
Response envelope: {ok: boolean, reply: string, status: done|clarification|confirmation|error, assumptions: string[], candidates?: array, confirmation_code?: string}.
Ordinary remove requests only preview. Only authenticated inbound /remover CODE can confirm_remove; confirm_remove must not be exposed as an LLM tool action.
Use requester-scoped, expiring, single-use confirmation records and reject changed item snapshots.
Legacy CLI behavior remains compatible; new plugin calls the sibling agent_api.py.
No direct gateway config writes, restarts, real-user sends or live database tests by subagents.

## Validation and rollout
Focused tests per lane, then full existing Grocery Python + plugin suites; Doctor and routing suites as affected.
Use synthetic data. Preserve allowlists. Record effective runtime, model and tool schema for model trials.
Choose models only from comparative evidence; keep Sol if evidence is insufficient.
Deploy only reviewed artifacts with backups, config validation and rollback instructions.

# tools/

The household tools run inside the OpenClaw gateway. Grocery is in active
use; Doctor is in early testing and is not fully launched. This directory
mirrors the agent workspace repo so it can be dropped back into
`~/.openclaw/workspace/` on a production machine:

| Here | Upstream | What |
|---|---|---|
| `tools/<name>/` | `workspace/tools/<name>/` | one directory per tool: `core/` engine and tests, `plugin/` OpenClaw adapter, `skill/` agent-facing behaviour |
| `tools/automations/` | `workspace/automations/` | scheduled workflows: the morning brief prompt and policy, the calendar `.ics` guardrail |
| `tools/docs/` | `workspace/docs/` | the layout plan, the command cheat sheet, and the `shadow-0914` refactor handoff |
| `tools/scripts/` | `~/tools/scripts/` | operator-side install and update scripts, and the doctor timers |

Dependency direction is one way: `skill → plugin → core`. A core never
imports its plugin. Runtime paths are set in the gateway configuration, never
supplied by the model.

## What runs where

| Tool | Engine | Adapter | Tests |
|---|---|---|---|
| `grocery-list` | Python, stdlib, SQLite | TypeScript plugin, seven narrow tools, `/remover` command | 402 core · 31 plugin |
| `doctor-search` | Python, stdlib, SQLite; three systemd user timers | TypeScript plugin, `doctor_search` tool, `/ok` command | 33 core · 11 plugin |
| `access` | | TypeScript plugin: people, identities, grants, trusted tool policy | 17 |
| `household-router` | Node, no dependencies | plugin: durable modes, native mode switches, domain ceiling | 17 |
| `household-config` | Python renderer + Node integration tests | generates per-domain agent instructions from `prompts/` | 24 |
| `media-transcription` | Bash + Python over `whisper.cpp` and `gst-launch` | `tools.media` entries in the gateway config | shell + Python |

Run what needs nothing installed:

```bash
cd tools
PYTHONPATH=grocery-list/core python3 -m unittest discover -s grocery-list/core/tests -p 'test_*.py'
(cd doctor-search/core && PYTHONPATH=. python3 -m unittest discover -s tests -p 'test_*.py')
node --test household-router/test/*.test.mjs
python3 household-config/test_prepare.py
python3 automations/calendar-ics-guardrail/test_calendar_ics_guardrail.py
```

The TypeScript plugins require the OpenClaw SDK and a TypeScript/Vitest
build environment. Access and Grocery declare development dependencies;
Doctor's exported manifest does not yet declare its build/test dependencies,
so `npm install && npm run build && npm test` is not a verified setup recipe
for every plugin. The counts above are historical; see the
[deployment validation](docs/shadow-0914/VALIDATION.md) and the
[September 15 export review](../docs/VALIDATION.md).

## Configuration that is deliberately not here

- `grocery-list/core/config/members.json` holds the household roster with
  phone numbers. Copy `members.example.json` and fill it in.
- `automations/google-digest/commitments.json` and `state.json` are the
  brief's private ledger. Copy `commitments.example.json`.
- `doctor-search/core/data/doctor.sqlite3`, the grocery databases and the
  access store are runtime state, created on first use.
- The doctor tool's email goes through a dedicated agent mailbox; its key
  file and address are configuration (`DOCTOR_BOT_EMAIL`).
- Media benchmark recordings were household voice notes and are not
  published; `media-transcription/benchmarks/` holds the harness only.

## De-identification

Phone numbers in tests and docs were already fictional upstream (a commit on
2026-09-13 guards tracked files against real ones). For publication, the
owner's and household members' names in fixtures and docs, a ZIP code, an
insurance plan name and the agent mailbox address were replaced with
synthetic values by the same export script that builds the rest of this
repo. Store names in fixtures are large chains and were kept.

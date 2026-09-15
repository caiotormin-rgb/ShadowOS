# ShadowOS

Shadow is a personal agent that lives on one workstation and answers on
every device I own. It runs on [OpenClaw](https://github.com/openclaw/openclaw),
speaks WhatsApp and Telegram, and carries a set of tools I built for the
things that kept costing me time: a shared grocery list, a doctor finder,
a morning brief, and read-only context over eighteen years of email and the
documents that matter. Three invited people use the household tools every
week. The agent cannot send mail, edit a calendar or delete a file without
me approving the exact action first.

Two private repos, about 210 commits, roughly 1,250 tests. This is the
public, de-identified cut.

## Motivation

I often postpone starting a task because of friction fatigue: fishing for
scattered information across apps and devices, each with its own login,
before the actual work can begin. The information was all there, in email,
in Drive, in documents dumped wherever they landed. Getting to it was the
problem. After a stretch of higher workload and stress I decided to build a
system that could make sense of my unstructured data instead of me doing it
by hand every time, and then to hand the same tools to my household.

## Approach

Index everything once, keep it current automatically, and make it reachable
from wherever I already am. The agent is on every device through a
Tailscale tailnet. New context is ingested natively as it arrives, so the
system organises intake going forward rather than only cleaning up the past.
Tools were added one at a time, each because a specific thing was annoying,
and each stayed only if people kept using it.

Three principles fell out of that:

- **Sources stay authoritative.** Gmail, Drive and Calendar are the truth;
  every index is a rebuildable cache.
- **Cheap, deterministic layers first.** A model only sees what survives the
  free filters, and it never supplies an identity, a path or a confirmation.
- **The agent cannot write on its own.** Anyone who can message it can do
  what it can do, so mutation goes through native commands a person types,
  and read-only is enforced by tests rather than policy.

## The agent

- **Channels.** Two WhatsApp accounts, one for me and one for the household
  with a four-person allowlist and groups disabled, plus a Telegram bot.
  A verified command owner and per-channel allowlists gate everything.
- **Access.** A household access plugin holds people, identities and grants.
  Each tool checks the requester's grant; an unknown sender, an inactive
  grant or an unreadable store denies. A mode router keeps a durable
  Grocery or Doctor mode per sender and switches on a single word
  (`lista`, `groceries`, `médico`, `doctor`) without a model call.
- **Models.** Cloud lanes selected per session, with a research model pinned
  separately from the chat model. A local `llama.cpp` lane and local
  `whisper.cpp` handle bounded private work with tool use disabled. Lighter
  models were trialled on a synthetic contract harness and not promoted.
- **Machine.** An isolated Linux account with no sudo, gateway on loopback
  with token auth, systemd user timers, and a working discipline for coding
  agents on a live box: dated evidence records, root steps as rehearsed
  handoffs, boundary assertions before every commit.

## The tools

| Tool | What it does | People | Tests |
|---|---|---|---|
| **Grocery list** | One live list per store, fed by text, voice notes, photos and short videos, in Portuguese or English. Trips close and roll over what is missing; products merge across languages; lists group by aisle in the store's walk order. Shared household lists plus per-requester private lists. Removal previews and executes only on a typed `/remover CODE`. 90-day retention of message text. | me + 3 | 402 core, 31 plugin |
| **Doctor finder** | Takes a household member's request, searches the web near their ZIP, reads practice sites, has the model extract facts and score the match with a quote, drops weak matches, ranks by distance and rating, and messages a shortlist. Can email a practice, but only after the requester verifies their address and approves the exact draft with `/ok CODE`. Replies are matched by request id and treated as untrusted. | me + 3 | 33 core, 11 plugin |
| **Household access and modes** | Grants, identities, monitor-then-enforce policy, `/access who`, `/me`; the mode router and its domain ceiling. | all | 17 + 17 |
| **Mail, ledger, documents** | Three read-only MCP servers over a Gmail metadata index, a transaction ledger keyed on counterparty, and a document catalog with extracted fields. `what did I pay the contractor?` returns every invoice and the total. | me | 719 |
| **Morning brief and calendar guardrail** | A read-only digest of what needs attention, and a model-free scan that turns `.ics` attachments into calendar events without copying attendees or sending updates. | me | |
| **Media capture** | Local transcription of voice notes and 15-second videos, language-aware, with a strict time and CPU budget so a long note cannot starve the others. | household | |

The context layers are under `layers/` and the household tools under
`tools/`, laid out so they can drop back into an OpenClaw workspace.
[docs/HOUSEHOLD-TOOLS.md](docs/HOUSEHOLD-TOOLS.md) walks through them.

## In use

The household tools went live for three invited people in early September.
The first real conversations found the bugs the tests had not:

- A due-items action failed on every call. A blank dedup key merged unrelated
  items. Both fixed the same day, with tests.
- Portuguese voice notes came back as English filler or nothing. Local
  transcription was rebuilt with language detection per chunk, a retry lane,
  and a time budget, then re-verified against the household's own samples.
- After a refactor, `/lista` succeeded but the next request asked the person
  to switch modes again. The router's denial message had described an
  unavailable tool as a missing mode, and the model repeated the story. A
  second bug sat underneath: the harness reaches tools through a broker the
  policies had not modelled. Both were reproduced in the real runtime, fixed,
  covered by 29 access and 24 router tests, and deployed with a backup and a
  written rollback.

## Numbers

| | |
|---|---|
| Commits | 160 in the agent workspace, 50 in the station repo |
| Tests | 535 across the household tools, 719 across the context layers |
| Mail indexed | 132,588 messages, 2008 to 2026 |
| Transactions | 1,813 events over 1,102 counterparties, 84% precision hand-checked |
| Model calls for 80% ledger coverage | about 154, one per sender, not 20,250 |
| MCP call, spawn to answer | 32 ms |

## In this repo

```
tools/             grocery-list, doctor-search, access, household-router,
                   household-config, media-transcription, automations, scripts
layers/openclaw/   mail-context, ledger, life-index, mail-enrichment,
                   calendar-context, drive-context: code, schema, tests, units
station/           operator scripts, boundary assertions, publish scanners
docs/              case study, architecture, security, household tools,
                   runbooks, conventions, the master plan and decision register
```

The tests need nothing installed and are the fastest way to read the code:

```bash
git clone https://github.com/caiotormin-rgb/ShadowOS && cd ShadowOS/layers/openclaw
for L in mail-context calendar-context drive-context; do (cd $L && PYTHONPATH=../mail-context python3 -m unittest discover -s tests -t .); done
for L in ledger life-index mail-enrichment;          do (cd $L && PYTHONPATH=../mail-context python3 -m unittest discover -s tests); done
```

- [How it came together, with the numbers](docs/CASE-STUDY.md)
- [The household tools](docs/HOUSEHOLD-TOOLS.md) · [Architecture](docs/ARCHITECTURE.md) · [Security](docs/SECURITY.md) · [History](docs/HISTORY.md)

## What's not here

This is a filtered export of two private repos. Implementation records, the
agent's memory and persona files, superseded plans, and every credential,
index and database stay private. Names, amounts, addresses, a ZIP code, a plan name and small vendors in
fixtures were replaced with synthetic ones; the household roster, the
brief's private ledger and the voice-note benchmarks are not published;
the per-sender ledger templates were pruned to household-name platforms. This is a proof
of work, not a packaged product: it runs on one machine and the runbooks
describe that machine.

MIT licensed.

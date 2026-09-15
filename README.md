# ShadowOS

A personal AI agent over eighteen years of email and the documents that
matter, answering from a phone, unable to send, edit or delete anything.
Stdlib Python, SQLite, systemd, three MCP servers, 719 tests.

My email had become my filing system by accident. This is what I built to
stop searching it by hand, and it grew one tool at a time rather than from a
plan. What's here is what survived.

## What it does

Ask from Telegram or WhatsApp:

| You ask | It answers |
|---|---|
| *what did I pay the contractor?* | every invoice with its number, and the total. Raw rows add up to nearly three times as much, because invoices get dunned before they are paid |
| *what subscriptions am I on?* | 189 rows grouped by vendor |
| *when did I last hear from that person?* | one counterparty, resolved across every address they have used |
| *where is the signed contract?* | type, date, the amount agreed, and a path. Not the text. |

Three tools sit behind that, each a stdio MCP server with four read-only
tools, answering in about 32 ms including process spawn:

- **mail-context** — a disposable local index of Gmail metadata. Never
  bodies. Delete it and it rebuilds in half an hour; Gmail stays the source
  of truth.
- **ledger** — one row per purchase, subscription, booking or payment, keyed
  on the counterparty rather than the sender address. Fast, structured
  context for an agent doing lookups on my behalf.
- **life-index** — a small catalog of the documents that actually matter,
  with amounts and IDs extracted as fields. Fed deliberately, so it stays
  small.

The same gateway serves a shared grocery list and a doctor finder to my
household through a dedicated WhatsApp account. Those are not in this repo
yet; a limited household view of the ledger is the next step, and the limits
are the design problem.

## How it's built

Plain Python and SQLite with nothing to install: there is no
`requirements.txt` because there is nothing in it. Everything runs as
systemd user timers under an isolated service account with no sudo, on one
Ubuntu workstation, behind [OpenClaw](https://github.com/openclaw/openclaw)
as the chat gateway. Cloud models do the talking; a local `llama.cpp` lane
handles bounded private tasks with tool use disabled.

The safety properties are tests, not promises. The Gmail grant is read-only,
but the real boundary is a transport with one GET method and a path
allowlist, plus tests that walk the package for anything else. Document text
for the sensitive tiers never reaches the chat surface, because a bot on
WhatsApp must not answer "what is my tax ID" for whoever holds the phone.

| Layer | State | Tests |
|---|---|---|
| [mail-context](layers/openclaw/mail-context/) | live, 132,588 messages, synced twice daily | 158 |
| [ledger](layers/openclaw/ledger/) | live, 1,813 events over 1,102 counterparties | 72 |
| [life-index](layers/openclaw/life-index/) | live for me, agent wiring staged | 73 |
| [mail-enrichment](layers/openclaw/mail-enrichment/) | sender classes, entity resolution, harvester, model benchmark | 33 |
| [calendar-context](layers/openclaw/calendar-context/) | built, not connected | 186 |
| [drive-context](layers/openclaw/drive-context/) | retired; Google's own Drive connector does it better | 197 |

Most of the tests exist because something went wrong once. The case study
has the list.

- [How it came together, with the numbers](docs/CASE-STUDY.md)
- [Architecture](docs/ARCHITECTURE.md) · [Security](docs/SECURITY.md) · [History and working method](docs/HISTORY.md)

## Running it

The tests need nothing installed and are the fastest way to read the code:

```bash
git clone https://github.com/caiotormin-rgb/ShadowOS && cd ShadowOS/layers/openclaw
for L in mail-context calendar-context drive-context; do (cd $L && PYTHONPATH=../mail-context python3 -m unittest discover -s tests -t .); done
for L in ledger life-index mail-enrichment;          do (cd $L && PYTHONPATH=../mail-context python3 -m unittest discover -s tests); done
```

Each layer's README covers authorising, syncing and wiring it to the
gateway. This is a proof of work, not a packaged product: it runs on one
machine and the runbooks describe that machine.

## What's not here

This is a filtered export of a private operations repo. The dated
implementation records, the agent's memory, superseded plans and every
credential and index stay private, and links into `records/` will not
resolve. Names, amounts, addresses and small vendors in fixtures were
replaced with synthetic ones, and the per-sender ledger templates were
pruned to household-name platforms.

MIT licensed.

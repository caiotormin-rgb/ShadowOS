# ShadowOS

An assistant that lives in the background of your paperwork. It reads
eighteen years of my email and every document I ever lost track of, answers
from my phone, and cannot send, edit or delete a thing.

## Why this exists

I can't keep files organised. I dump them anywhere. So over the years my
email became the filing system by accident: bank details, contracts, school
forms, invoices, all of it somewhere in Gmail and scattered
across devices. When I needed something I'd search for one specific email
from ten years ago that had exactly the information, and nothing else.

It works, but it takes time, and being asked for something on the spot was
the worst part. I lost track of what I'd paid a contractor because the
invoices came irregularly and I could never find the right one. That was the
question that started this: *what did I pay the contractor?*

None of this was planned as a product. Each piece was built because a
specific thing was annoying, and it stayed if it kept being useful. What's
here is what survived.

## What it does now

Ask it from Telegram or WhatsApp:

| You ask | It answers |
|---|---|
| *what did I pay the contractor?* | every invoice with its number, and the total. The raw rows add up to nearly three times as much, because invoices get dunned before they are paid |
| *what subscriptions am I on?* | 189 rows grouped by vendor, including the ones I forgot |
| *when did I last hear from that person?* | one counterparty, resolved across every address they have used |
| *where is the signed contract?* | type, date, the amount we agreed, and a path. Not the text. |

Behind that are three small tools, each with its own reason to exist:

- **mail-context** is a local index of Gmail metadata: who, when, subject,
  snippet. Never bodies. It is the thing everything else reads from, and it
  is disposable: delete it and it rebuilds in half an hour. Gmail stays the
  source of truth.
- **ledger** turns that index into one row per purchase, subscription,
  booking, appointment or payment, keyed on the counterparty rather than the
  sender address, because Amazon mails from 21 addresses and I think in
  "Amazon". It exists so an agent doing a lookup on my behalf has fast,
  structured context instead of a mailbox to dig through.
- **life-index** is a small catalog of the documents that actually matter:
  contracts, official forms, school records, identity documents. It pulls the
  amounts and IDs out into fields so "what did we agree to pay" is a value,
  not a page number. It is the one part that needs a deliberate act to feed,
  which is why it stays small.

The same gateway runs two household tools, shared through a dedicated
WhatsApp account: a shared grocery list and a doctor finder. They
are not in this repo yet. Calendar and a limited view of the ledger are next
for them.

Each tool is exposed to the chat agent as a stdio MCP server with four
read-only tools. A call, including spawning the server, takes about 32 ms.

## How it's built

Plain Python and SQLite, nothing to install. There is no `requirements.txt`
because there is nothing in it. Gmail is reached over REST with the standard
library. Everything runs as systemd user timers under a service account with
no sudo, on one Ubuntu workstation, behind
[OpenClaw](https://github.com/openclaw/openclaw) as the chat gateway. Cloud
models do the talking; a local `llama.cpp` lane handles bounded private
tasks with tool use disabled.

The part I care most about is that the safety properties are tests, not
promises. The Gmail grant is read-only, but the real boundary is a transport
with one GET method and a path allowlist, plus tests that walk the package
for anything else. Document text for the sensitive tiers is withheld from
the chat surface entirely, because a bot reachable from WhatsApp must not be
able to answer "what is my tax ID" for whoever is holding the phone.

719 tests across the six layers. A lot of them exist because something went
wrong once: a column that read zero for every row, a unit that sat two
releases behind, a classifier reading two years of rules against eighteen
years of mail. Each one became a test rather than a note to self.

| Layer | State | Tests |
|---|---|---|
| [mail-context](layers/openclaw/mail-context/) | live, 132,588 messages, synced twice daily | 158 |
| [ledger](layers/openclaw/ledger/) | live, 1,813 events over 1,102 counterparties | 72 |
| [life-index](layers/openclaw/life-index/) | live for me, agent wiring staged | 73 |
| [mail-enrichment](layers/openclaw/mail-enrichment/) | live tooling: sender classes, entity resolution, harvester, model benchmark | 33 |
| [calendar-context](layers/openclaw/calendar-context/) | built, not connected | 186 |
| [drive-context](layers/openclaw/drive-context/) | retired; Google's own Drive connector does it better | 197 |

More detail: [how it fits together](docs/ARCHITECTURE.md), [what keeps it
safe](docs/SECURITY.md), [how it came together and what went wrong](docs/CASE-STUDY.md),
[the commit log and working method](docs/HISTORY.md).

## Run it yourself

You need Linux, Python 3.12, SQLite with FTS5, and your own Google OAuth
Desktop client with the `gmail.readonly` scope. Optional: `pdftotext` for
document text, `gocryptfs` for an encrypted store, an OpenClaw gateway for
the chat surface.

```bash
git clone https://github.com/caiotormin-rgb/ShadowOS
cd ShadowOS/layers/openclaw

# The tests need nothing installed. Run them first.
(cd mail-context     && python3 -m unittest discover -s tests -t .)
(cd ledger           && python3 -m unittest discover -s tests)
(cd life-index       && python3 -m unittest discover -s tests)
(cd mail-enrichment  && PYTHONPATH=../mail-context python3 -m unittest discover -s tests)
(cd calendar-context && PYTHONPATH=../mail-context python3 -m unittest discover -s tests -t .)
(cd drive-context    && PYTHONPATH=../mail-context python3 -m unittest discover -s tests -t .)
# Seven life-index tests skip here: they replay a schema migration against a
# commit that exists only in the private history.

# Gmail. Put your client_secret JSON where authorize tells you.
cd mail-context
python3 -m mailctx.authorize            # one-time consent; refuses a wider grant
python3 -m mailctx.syncrun --status
python3 -m mailctx.syncrun              # incremental; see the layer README for a full load

# Ledger, from the index. Rules per sender live in templates.py.
cd ../ledger
LEDGER_SELF_ADDRESSES=you@example.com python3 run_extract.py /tmp/ledger-out --snapshot ~/.local/state/mail-context/mail-context.sqlite
python3 build_ledger.py /tmp/ledger-out ~/.local/state/ledger/ledger.sqlite
python3 demo_queries.py ~/.local/state/ledger/ledger.sqlite

# Documents.
cd ../life-index
bin/li-plaintext                        # or bin/li-init for a gocryptfs store
cp ~/Downloads/contract.pdf ~/life-index/plain/consume/
python3 -m lifeindex.cli consume
python3 -m lifeindex.cli search contract

# Give the agent the tools. Each is a stdio MCP server.
python3 -m mailctx.mcp                  # in mail-context
python3 mcp.py                          # in ledger
python3 -m lifeindex.mcp                # in life-index
```

Registering one with OpenClaw, shown for mail-context. Pin the tool list so a
future addition is not silently handed to the chat agent:

```bash
openclaw mcp add mail-context \
  --command python3 --arg -m --arg mailctx.mcp \
  --cwd /path/to/layers/openclaw/mail-context \
  --include mail_search,mail_thread,mail_candidates,mail_status
```

The runbooks under [docs/runbooks/](docs/runbooks/) describe the real
deployment: the isolated account, the timers, and the handoff procedure for
anything that needs root.

## What's not here

This is a filtered export of a private operations repo. Left out on purpose:
the dated implementation records (they name real properties, prices and
people), the agent's memory, superseded plans, and every credential, index
and token. Links into `records/` from the runbooks point at that private
evidence and will not resolve. `torm` and `caio` are the real hostname and
my account; they stayed so the docs stay true to the machine they describe.
Names, amounts, addresses and small vendors in test fixtures and comments
were replaced with synthetic ones before publishing, and the per-sender
ledger templates were pruned to household-name platforms; the private copy
has the full set.

MIT licensed. See [LICENSE](LICENSE).

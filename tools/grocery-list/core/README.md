# Grocery list — quick guide

The utility keeps one live list per store. Text, voice, image, and video are
interpreted by OpenClaw; the resulting clean items and their source are stored
locally with timestamps.

## Everyday commands in chat

- “Add milk and two dozen eggs to Costco.”
- “Add the groceries in this photo to ShopRite.”
- “Mark milk bought at Costco.”
- “What is still needed at Trader Joe's?”
- “Close the Costco trip.”
- “Share the Costco list with Mom.”

Closing a trip archives a dated snapshot, removes purchased items from the live
list, and keeps anything missing on the next list.

Sharing is denied unless the contact is on the local allowlist. Rendering a
share does not send it; OpenClaw must receive an explicit share request, deliver
it using the configured channel, and then record successful delivery.

## Who may use the list

The household is described in one file, `config/members.json`, copied from
`config/members.example.json`. It holds personal phone numbers, so the real
file is gitignored and only the example is committed.

```json
{ "name": "Kim", "phone": "+5511900000021", "role": "member", "lang": "pt" }
```

```bash
python3 tools/grocery-list/core/grocery.py members show          # read it, change nothing
python3 tools/grocery-list/core/grocery.py members sync          # apply it
python3 tools/grocery-list/core/grocery.py members sync --prune  # also revoke anyone removed
```

Numbers are normalized to E.164, so `+55 11 90000-0021` and `+5511900000021`
are one person rather than two histories. Applying is idempotent. Revoking is
opt-in: an entry deleted from the file is reported but not acted on until
`--prune`, because that is the one step re-running cannot undo.

The phone number is the identity everywhere — membership, who added what, and
which language to answer in.

**Access closes once configured.** With nobody enrolled, anything is allowed,
so a fresh install is not locked out of its own list. After the first member is
added, an unknown number is refused and so is an unidentified caller.

This is the list-level allowlist. It is separate from the gateway allowlist in
OpenClaw, which decides who may message the bot at all; `members sync` returns
the numbers under `gateway_allow_from` for that purpose but does not apply them.

## One-time setup

Initialize the local database:

```bash
python3 tools/grocery-list/core/grocery.py init
```

Allow a contact after confirming the exact channel and target:

```bash
python3 tools/grocery-list/core/grocery.py allow add Mom \
  --channel whatsapp \
  --target +15551234567
```

Inspect the allowlist:

```bash
python3 tools/grocery-list/core/grocery.py allow list
```

## Direct CLI use

Add ordinary text items:

```bash
python3 tools/grocery-list/core/grocery.py add --store Costco "milk" "eggs"
```

Agent-normalized multimodal input:

```bash
python3 tools/grocery-list/core/grocery.py ingest \
  --store Costco \
  --source-type image \
  --source-ref fridge.jpg \
  --items-json '[{"name":"Milk","quantity":2,"note":"2%"}]'
```

Online product links follow the same trust boundary: OpenClaw fetches or
scrapes the submitted page with its web tools, extracts the product name and
the page's canonical URL, and passes only that normalized data to this local
utility. The utility does not make network requests.

```bash
python3 tools/grocery-list/core/grocery.py ingest \
  --store Amazon \
  --source-type url \
  --source-ref 'https://amazon.com/gp/product/B012345678?tag=shared' \
  --items-json '[{"name":"Coffee beans","productUrl":"https://www.amazon.com/dp/B012345678"}]'
```

`productUrl` is optional for backward compatibility. When present it must be
an absolute HTTP(S) URL without embedded credentials. The utility lowercases
the scheme and host, removes default ports and fragments, and otherwise keeps
the path and query. A later ingestion of the same item with a new canonical
URL updates the link; ingestion without a link preserves the stored one.

Shop and close:

```bash
python3 tools/grocery-list/core/grocery.py buy --store Costco "Milk"
python3 tools/grocery-list/core/grocery.py close --store Costco
python3 tools/grocery-list/core/grocery.py history --store Costco
```

Prepare an allowlisted share without sending it:

```bash
python3 tools/grocery-list/core/grocery.py share --store Costco --contact Mom
```

The result contains a `share_id`, destination, and payload. After OpenClaw
successfully delivers it, record that fact:

```bash
python3 tools/grocery-list/core/grocery.py delivered 1
```

## Local test environment

A browser UI for exercising the list by hand, instead of typing CLI commands:

```bash
python3 grocery-list/webtest.py --port 8765
```

It binds `127.0.0.1` only and is a development tool, not a deployment. Pass
`--db` to point it at a scratch database rather than the live one.

## Language

`--lang en|pt` sets the language of everything the CLI renders: headers,
section labels, confirmations, assumption clauses, and timestamps. Item names
are always stored and shown exactly as the person wrote them.

Resolution order: an explicit `--lang`, then the requester's stored
preference, then `GROCERY_LANG`. Never the language of the items — a message
that *is* a list has no prose to read, and guessing from the items is a coin
flip. Record a preference once and the question stops arising:

```bash
python3 tools/grocery-list/core/grocery.py who set owner --lang pt --name "the owner"
python3 tools/grocery-list/core/grocery.py list --actor owner --format text
```

The same list answers `4 na lista` to the owner and `4 needed` to an English
speaker, with item names untouched in both.

## Sections and store layout

Items are grouped into store sections for walking the aisles. A section is
resolved from what the household has already taught the app, then from the
curated bilingual map, and is otherwise unknown — `classify` never guesses,
because a wrong answer would be remembered forever.

```bash
python3 tools/grocery-list/core/grocery.py list --format text --lang pt
python3 tools/grocery-list/core/grocery.py section unknown          # what needs teaching
python3 tools/grocery-list/core/grocery.py section set "Nutella" snacks --source agent
python3 tools/grocery-list/core/grocery.py layout --store Costco --set warehouse
```

Learned sections are keyed by product name, not item id, so they survive the
item being bought and removed. `supermarket` walks produce first with staples
at the back; `warehouse` opens on non-food and keeps perishables last.

## What is missing

The event log supports asking what the household usually buys that is not on
the list right now — "am I missing something from produce?"

```bash
python3 tools/grocery-list/core/grocery.py due --format text
python3 tools/grocery-list/core/grocery.py due --section produce --format text --lang pt
python3 tools/grocery-list/core/grocery.py stats
```

Only confirmed purchases count, meaning items that were still marked bought
when a trip closed — `buy` followed by `unbuy` is not a purchase. A product
bought once has no rhythm and is never suggested; suggesting from a single
purchase is guessing rather than remembering. `--slack` widens the interval
before something counts as late, so a weekly item is not nagged about on day
six.

## One product, two languages

A bilingual household writes the same product two ways. Items are matched on a
canonical key from a curated synonym map, so `leite` and `milk` are one row,
and `buy milk` finds the item someone added as `Leite`. The spelling first
written is the one kept — whoever added it sees their own words.

The map errs toward not merging. Qualified products get their own keys, so
`leite de coco` never collapses into `leite`, and genuinely ambiguous words are
left alone: bare `pasta` means a folder in pt-BR far more often than the food,
and `salsa` is parsley or sauce depending on the language. An under-merge
leaves two rows and is annoying; an over-merge silently replaces someone's
item, so the failure directions are not comparable.

Units still separate: `leite (gal)` and `milk (L)` remain two rows.

## Current boundary

- Data stays in `tools/grocery-list/core/data/grocery.sqlite3`.
- Duplicate names within a store are merged; the highest requested quantity is
  kept so repeated household requests do not accidentally double an item.
- Every ingestion source is retained for auditability.
- OurGroceries sync is out of scope by choice, not pending work. There is no
  public API or OAuth flow; every integration is unofficial, reverse-engineered
  from the web app and authenticated with the account password. Sharing stays a
  rendered payload delivered over an allowlisted channel.

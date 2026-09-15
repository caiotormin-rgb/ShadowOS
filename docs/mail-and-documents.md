# Mail & documents

One system across two sources. Gmail and Google Drive both hold things worth
finding; neither is complete, and the split is not where you'd guess.

- **Gmail** has the transactions — receipts, subscriptions, bookings,
  appointments, payments. It has almost none of the identity documents.
- **Drive** has the documents — contracts, official forms, letters,
  scans. It has none of the transaction history.

Measured, not assumed: of 23 curated document-looking emails, only 5 carried a
real document. The rest were *pointers* — "your W-2 is available" — whose
content lives behind a login. Meanwhile Drive held documents that never touched email at all.

---

## The three surfaces

### 1. Ask the agent (Telegram / WhatsApp)

The weekly path. No filing, no terminal.

> **what did I pay Acme Lawn?**
> 19 invoices, each with its number, and the total.

> **what subscriptions am I on?**
> 189 rows — Apple, Google, Anthropic, GitHub, and the ones you forgot.

> **when did I last hear from <person>?**
> One counterparty, resolved across every address they've ever used.

Totals come from a deduplicated view. The same question against raw rows
answers nearly three times the true total, because invoices get dunned eleven
times before they're paid.

### 2. Search from the shell

```bash
mailq escritura <street>          # full-text over 18 years, milliseconds
mailq --from acme              # by sender, substring match
mailq --sent --since 2010         # what you wrote that year
mailq --attachments --since 2020  # messages carrying files
mailq --senders --limit 20        # who mails you most
mailq --years                     # volume by year
```

Reads a snapshot, so no sudo and a slow query can't disturb the sync.
`mailctl snapshot` refreshes it.

### 3. Catalog a document

```bash
cp ~/Downloads/lease.pdf ~/life-index/plain/consume/
python3 -m lifeindex.cli consume       # hash → extract → classify → tier → index
python3 -m lifeindex.cli search escritura
python3 -m lifeindex.cli get <sha256>  # metadata, extracted fields, file path
```

Extracted fields are the point: *"what did we agree to pay"* returns a value,
not a page number.

### Drive needs no ingestion at all

Google's own MCP connector does search, full text and OCR — it read a
handwritten-annotated 2020 scan cleanly, extracting the surveyor, licence
number and fee. So Drive files are cataloged in place — metadata, extracted
text and a link, but never the bytes. A discovery sweep classified 233 files
and produced a ranked review queue; the 32 approved keepers were ingested on
2026-08-25.

A few are **pointers only**: they carry government ID numbers, and the
store is still plaintext on an unencrypted disk, so the catalog holds their
tier, type, date and Drive link and none of their text. That is enough to file
them and not enough to leak them.

---

## How a message becomes an answer

```
Gmail listing query          ── promos/social never fetched
        ↓
sender class                 ── kills 68% of the rest, free
        ↓
structural gate              ── labels, attachment shape
        ↓
learned sender template      ── free after the first time
        ↓
local model, once per sender ── ~154 templates cover 80% of the corpus
        ↓
transaction row ─→ ledger.sqlite ─→ MCP ─→ your phone
```

Each layer either terminates or extracts — never merely annotates, or volume
never drops. Decisions cache per **sender**, not per message: learning NJ
Transit's receipt format once serves all 102 of them forever.

---

## What it holds today

| | |
|---|---|
| Mail indexed | 132,588 messages, 2008 → 2026 |
| Sent mail | 5,623 (313 of them in the last two years) |
| Counterparties resolved | 1,102 from 1,973 addresses |
| Transactions | 1,813 deduplicated events |
| Documents cataloged | 55 — 23 from Gmail, 32 from Drive (mail backfill pending) |
| Drive files classified | 233; 32 approved keepers ingested 2026-08-25 (18 tier-1) |

Amounts are present on 505 of 1,813 rows. That is snippet truncation, not
extraction failure — the phrases the templates match survive in the full body,
so fetching bodies should lift it sharply with no template changes.

---

## Boundaries

**Read-only, by construction.** The grant is `gmail.readonly`, but Gmail has no
draft-without-send scope, so the real boundary is a transport allowlist plus a
test asserting every HTTP call in the package is a GET.

**Tier-1 text is withheld from the agent.** The gateway answers from
WhatsApp; *"what is my CPF"* must not be answerable by anyone who can message
it. Search returns metadata, extracted fields, and a path.

**Everything is rebuildable except the documents.** Gmail stays authoritative.
Deleting the index costs half an hour.

## Operating it

```bash
mailctl status        # index, timer, and whether the prune would eat your data
mailctl sync          # incremental catch-up (the timer does this twice daily)
mailctl shred         # destroy local plaintext when you're done
```

Full plan and decision register:
[`plans/00-mail-and-documents-MASTER.md`](plans/00-mail-and-documents-MASTER.md).

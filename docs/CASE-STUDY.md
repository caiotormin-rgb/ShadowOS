# How ShadowOS came together

*Caio Tormin. August to September 2026, evenings, one workstation, built
with coding agents under a written set of rules.*

## The problem

I don't keep files organised, so email became the archive: bank details,
contracts, school forms, invoices, findable by searching for one specific
message from years ago. It worked until it didn't. I lost track of what I
had paid a contractor whose invoices came irregularly, and being asked for
that number on the spot was the push. The first question the system had to
answer was *what did I pay the contractor?*

The obvious approach, hand the mailbox to a model, fails three ways: it is
expensive at 132,588 messages, too slow for a chat app, and dangerous, since
a chat-reachable agent with a mailbox grant is one prompt injection away
from mailing someone your tax return.

## What got built

- **A Gmail metadata read model.** Sender, date, subject, snippet, labels.
  No bodies. Synced twice a day under an isolated service account,
  rebuildable in half an hour. Read-only in four independent ways: the
  OAuth scope, a transport with one GET method and a path allowlist, a
  schema with nothing to write from, and tests that walk the package for
  anything else.
- **A transaction ledger keyed on counterparty.** Amazon mails from 21
  addresses and Apple from 38; the unit I think in is the merchant. A
  public-suffix-aware resolver maps every address to one key, shared with
  the document catalog so the two stores can join.
- **A parsing cascade that caches per sender.** 41 senders cover half of
  non-promotional mail, 154 cover 80%. Learn a receipt format once and it
  serves every receipt from that sender forever. Each layer may only
  terminate or extract, never merely annotate, or volume never drops. Model
  budget for 80% coverage: about 154 calls, not 20,250.
- **A document catalog with extracted fields.** Hash, extract text,
  classify, pull amounts and IDs into fields so "what did we agree to pay"
  is a value and not a page number. Fed deliberately, so it stays small.
- **Three MCP servers, twelve read-only tools.** Stdlib only in the query
  path, which is what buys a 32 ms cold start.
- **A household surface.** A grocery list and a doctor finder for my family
  through a dedicated WhatsApp account. The mail and ledger tools stay
  owner-only until per-requester scope exists.

Some things were built and killed: metadata digest views (the envelope, not
the letter), a custom Drive index (Google's connector already did search,
full text and OCR), a local model for sender classification (0.864
agreement, but a deterministic pass solved it for free). Each is in the
decision register as dead, with the reason.

## Numbers, measured on the real corpus

| | |
|---|---|
| Messages indexed | 132,588, 2008 to 2026 |
| Counterparties | 1,102, from 1,973 sender addresses |
| Transactions | 2,374 rows, 1,813 events after dedup |
| Extraction precision | 84%, hand-verified on 50 random rows |
| Threads that are a single message | 98.8%, so the message is the unit |
| Model calls for 80% coverage | about 154, one per sender |
| MCP call, spawn to answer | 32 ms p50 |
| Ledger query | 0.13 ms |
| Tests | 719 across six layers |

The acceptance test was a phone. "What did I pay the contractor?" from
Telegram returns every invoice and the total. Against raw rows it returns
nearly three times as much, because invoices get dunned before they are
paid. The deduplication is the product.

## What went wrong, and what it changed

- **A deployed unit sat two releases behind for two days** because deploying
  depended on a documented "remember to" step. Units use `%h` now, deploy
  with a plain `cp`, and a check script fails the commit if a literal home
  path returns.
- **A boolean column read zero for every row** because the sync fetched
  metadata only. The shell tool built on it confidently said "nothing
  found" for eighteen years of attachments. Replaced with a three-valued
  view, `yes | no | unknown`, and a test that the SQL and Python rules agree.
- **Production classified eighteen years of mail with two years of rules**
  because it read a file from a frozen evidence directory. Now a numbered
  decision: derived data lives in state, never in records.
- **A summary of the conventions dropped two of eight rules**, and the
  agent that trusted the summary broke exactly those two. The entry point
  now links the full text instead of summarising it.
- **The encryption gate is still waived.** The disk is plain ext4. The check
  fails on its merits and passes only through a recorded waiver that every
  status response surfaces. The migration is one command; I have not run it.

## Working with coding agents on a live machine

The workstation also hosts the production chat agent, so an agent editing
the wrong thing could take the bot down or move a credential. The rules
that made it workable are in the repo and are probably the most reusable
part:

- `AGENTS.md` says what is true about the machine, never what is intended,
  and leads with the two facts that bite: no passwordless sudo, and the
  machine is an SSH target with no outbound credentials.
- Every non-trivial change gets a dated record with the actual command
  output. Records are private because they are specific.
- Root-requiring steps become handoff files with the exact commands and the
  verification, rehearsed in a scratch prefix first, rollback included.
- A decision register with dead entries: 26 decisions, each with a status
  and a reason.
- Assertions instead of remembered steps: two read-only checks run before
  every commit and fail if the account boundary or a unit's paths drift.

## Next

1. Fetch message bodies. Amounts on 28% of ledger rows is a snippet limit,
   and bodies fix it with no template changes.
2. Wire the document catalog's MCP server to the gateway.
3. Design the household view of the ledger: verified command owner, channel
   allowlist, per-requester scope.
4. Run the encryption migration and retire the waiver.

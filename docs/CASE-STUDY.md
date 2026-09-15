# How ShadowOS came together

*Caio Tormin. August to September 2026, evenings, one workstation, built
with coding agents under a written set of rules.*

## Where it started

I don't keep files organised. Everything important ended up in email
because that is where it arrived, and email search was good enough to get
it back most of the time: one specific message from years ago with exactly
the detail I needed. Bank details, contracts, school forms, invoices.

Most of the time. The failure that finally pushed me was a contractor. The
invoices came irregularly, some by email, some not, and at some point I
genuinely did not know what I had paid. Being asked for a number like that
on the spot and not having it is a specific kind of bad. So the first
question the system had to answer was the one I couldn't: *what did I pay
the contractor?*

Nothing here was planned as a product. Each tool was built because a
specific thing was annoying. Some were built, tried, and killed. This
document is the honest version of that, with the numbers.

## What got built, in the order it mattered

**The mail index came first** because everything else needs it. A local
SQLite copy of Gmail metadata: sender, date, subject, snippet, labels.
No bodies. 132,588 messages over eighteen years, synced twice a day, and
disposable: delete it and it rebuilds in half an hour, because Gmail stays
the source of truth. It runs as an isolated service account with no sudo,
and the grant is read-only in four independent ways (the OAuth scope, a
transport with one GET method, a schema with nothing to write from, and
tests that walk the package for anything else).

**Then a lot of scoping that mostly got thrown away.** I went deep on what
every piece of mail metadata could tell me: people digests, purchase
digests, service digests, a browsable page. Built it, looked at it, and it
was not useful. Metadata is the envelope, not the letter. Around the same
time Google's own connectors for Gmail, Calendar and Drive turned out to
give good enough context for a fraction of the complexity, so the custom
Drive layer was retired the day it was proven unnecessary. Both are in the
decision register as dead, with the reason.

**The ledger is what survived.** One row per purchase, subscription,
booking, appointment or payment, keyed on the counterparty rather than the
sender address. It exists so an agent doing a lookup on my behalf has fast,
structured context rather than a mailbox to dig through. What made it
tractable was measuring the corpus before designing the extractor:

- 98.8% of threads are a single message, so the message is the unit and
  thread is a nullable grouping field.
- 41 senders cover half of non-promotional mail; 154 cover 80%. So
  decisions are made once per *sender*, cached as data, and the model
  budget for 80% coverage is about 154 calls rather than 20,250.
- Each layer of the cascade may only terminate or extract, never merely
  annotate, or volume never drops. The listing query drops promotional mail
  before any id is fetched; sender class kills 68% of what is left for free.

The result: 2,374 rows from a 3,958-message transactional pool,
deduplicated to 1,813 events, 84% precision hand-checked on 50 random rows
against the source mail. "What did I pay the contractor" returns 19 invoices
and the total. Against raw rows it returns nearly three times as much,
because invoices get dunned before they are paid. The deduplication is the product.

**The document catalog is the small, deliberate one.** Contracts, official
forms, school records, identity documents. Hash, extract text, classify, pull out the
amounts and IDs as fields, so "what did we agree to pay"
is a value and not a page number. 55 documents. It needs a deliberate act
to feed, which is why it stays small and why the ledger, which feeds
itself, is the weekly tool.

**Sharing with family came from a different direction.** The same gateway
serves a shared grocery list and a doctor finder to my household through a
dedicated WhatsApp account I set up just to be the door into these tools.
Those two are not in this repo yet. Calendar and a limited view of the
ledger are the next things they get, and the limits are the interesting
part: the bot must not answer "what is my tax ID" for whoever is holding
the phone, so document text for the sensitive tiers is withheld from the
chat surface entirely, and any family view of the ledger will be a subset.

## Numbers, measured on the real corpus

| | |
|---|---|
| Messages indexed | 132,588, 2008 to 2026, resync about 30 minutes |
| Counterparties | 1,102, resolved from 1,973 sender addresses |
| Transactions | 2,374 rows, 1,813 events after dedup |
| Extraction precision | 84%, hand-verified on 50 random rows |
| Model calls for 80% coverage | about 154, one per sender |
| MCP call, spawn to answer | 32 ms p50 |
| Ledger query | 0.13 ms |
| Documents cataloged | 55 |
| Tests | 719 across six layers |

Where a model sits was also decided by measuring. A local model was
benchmarked at 0.864 agreement on sender classification, then dropped
because a deterministic pass solved it at zero cost. Only 28% of ledger
rows carry an amount, and reading the source showed that is snippet
truncation, so fetching bodies comes before any new model lane.

## What went wrong

- **A deployed unit sat two releases behind for two days** because
  deploying it depended on a documented "remember to rewrite this path"
  step. The fix was not a better document. Units use `%h` now, deploy with a
  plain `cp`, and a check script fails the commit if a literal home path
  comes back.
- **A boolean column read zero for every one of 132,588 rows** because the
  sync fetched metadata only and the MIME walk that fed it never fired. The
  shell tool built on it confidently returned "nothing found" for a mailbox
  with eighteen years of attachments. It was replaced with a three-valued
  view, `yes | no | unknown`, with `unknown` as the default and a test that
  the SQL and Python rules agree.
- **Production classified eighteen years of mail with two years of rules**
  because it read a file out of a gitignored evidence directory that had
  been frozen at the 24-month window. Now a numbered decision: derived data
  lives in state, never in records.
- **A summary of the working conventions left out two of eight rules**, and
  the agent that trusted the summary broke exactly those two. The entry
  point now links the full text instead of summarising it.
- **An uncommitted record was destroyed** while several agent sessions ran
  at once. Records get committed the day they are written now.
- **Two bugs were found by agents in code I had already handed over**: an
  import that would have died on line 8 inside the operator's sudo session,
  and a harvester that recorded every failure, so one expired token would
  have marked the untouched remainder failed forever. Both have tests.
- **The encryption gate is still waived.** The disk is plain ext4 with a
  plain swapfile. The check fails on its merits and passes only through a
  recorded waiver whose text is surfaced in every status response. A
  gocryptfs init was run on the document store and never completed; the
  migration script is one command. I have not run it yet.

## Working with coding agents on a live machine

The workstation also hosts the production chat agent, so an agent session
editing the wrong thing could take the bot down or move a credential. The
rules that made this workable are in the repo and are probably the most
reusable part:

- `AGENTS.md` says what is true about the machine, never what is intended,
  and leads with the two facts that bite: no passwordless sudo, and the
  machine is an SSH target with no outbound credentials.
- Every non-trivial change gets a dated record with the actual command
  output. "Installed X" without the log does not count. Records are private
  because they are specific; that is what makes them useful.
- When a step needs root, the agent writes the exact commands and the
  verification into a handoff file and stops. Several were rehearsed end to
  end in a scratch prefix first, including the rollback.
- A decision register with dead entries: 26 decisions, each with a status
  and a reason, because direction changed three times in one day and stale
  decisions were still lingering in other documents.
- Assertions instead of remembered steps. Two read-only checks run before
  every commit and fail if the gateway's account boundary or a unit's paths
  drift.

## Next

1. Fetch message bodies. Amounts on 28% of rows is a snippet limit, and
   bodies fix it with no template changes.
2. Wire the document catalog's MCP server to the gateway. Verified over
   stdio; needs a terminal.
3. Bring the grocery list and doctor finder into the repo, and design the
   family view of the ledger properly: verified command owner, channel
   allowlist, per-requester scope.
4. Run the encryption migration and retire the waiver.
5. A local model lane for the long tail of senders, scored by the existing
   calibration benchmark, once bodies are in.

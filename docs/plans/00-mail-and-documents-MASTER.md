# Mail & documents — master plan

**2026-08-25. This is the single authoritative plan.** Everything else in
`docs/plans/` about mail or documents is either superseded or scoped beneath
this. Written because the direction changed repeatedly on 2026-08-24 and stale
decisions were still lingering in other documents.

---

## Part 1 — Decision register

Every decision taken, and whether it still stands. **If a decision is not in
this table, it is not a decision.**

| # | Decision | Status | Why |
|---|---|---|---|
| 1 | Gmail metadata index (mail-context), 64k messages, read-only, twice daily | **LIVE** | Working. The plumbing everything else targets. |
| 2 | Metadata-derived context views (people/purchases/services digest) | **DEAD** | Built, operator rejected: "not really useful, don't invest further." Metadata is the envelope, not the letter. |
| 3 | Browsable digest page as a product surface | **DEAD** | One-off only. No regeneration loop. |
| 4 | Document archive as the *primary* product | **DEMOTED** | Built and works; serves the least-frequent need (#4 of 6). Now the once-a-year path. |
| 5 | **Entity ledger as the primary product** | **LIVE — PRIMARY** | 5 of 6 stated retrieval needs are answered by transactional email bodies, not attachments. Weekly use. |
| 6 | Thread as the unit of work | **DEAD** | 98.8% of threads are single messages. The **message** is the unit; thread is a nullable grouping field. |
| 7 | Encryption at rest (gocryptfs) before documents land | **DEFERRED** | Verified working, no sudo needed. Operator: "not ready yet, this is a local setup." Store runs in explicit, labelled plaintext with a one-command migration. |
| 8 | Manual consume directory as the daily filing path | **DEMOTED** | Requires a habit that competes with email's zero effort. Now a one-time backfill for identity documents only. |
| 9 | Build our own Drive ingestion | **DEAD** | Google Drive MCP is already connected and does search + full-text + OCR. Verified on a handwritten-annotated 2020 scan. |
| 10 | Install Docling + tesseract for OCR | **DEFERRED** | Drive MCP OCRs Drive files. Only needed for Gmail attachments that are scans and never reached Drive. |
| 11 | Run paperless-ngx | **DEAD** | Five containers, IMAP-password ingestion we don't have, wants to own the archive. Its *ontology* was adopted. |
| 12 | Local model benchmark infrastructure (shared weights, qcbench) | **KEPT** | Was capability-driven, but transfers directly to ledger extraction, which is where judgment is genuinely needed. |
| 13 | Local model for sender classification | **DEAD** | Solved deterministically in phase 0 at zero cost. Benchmarked at 0.864 agreement — good, but unnecessary. |
| 14 | Gmail sending / drafting | **NOT NOW** | Requires `gmail.compose`, which also authorises sending. Draft-only design recorded; do not build until the ledger exists. |
| 15 | 24-month window | **SUPERSEDED** | Expanding to lifetime (~269k messages), junk excluded at the listing call. |
| 16 | **Multilayer parsing cascade, decisions cached per sender** | **LIVE** | 41 senders cover 50% of non-marketing mail, 154 cover 80%. Budget is ~154 model calls, not 20,250. |
| 17 | Bulk Gmail attachment sweep | **LIVE** | Reinstated deliberately: a wide first pass so filters can be calibrated from real output rather than guesses. |
| 18 | Continuous/ongoing bulk processing | **NO** | The sweep is one-off. Its purpose is to learn the threshold; a permanent wide sweep is waste. |
| 19 | **Fetch bodies before adding model lanes** | **LIVE** | Measured: only 23% of extracted rows carry an amount, and Amazon has 754 rows with zero. Snippet truncation, not extraction failure — bodies lift amounts with no template changes. Bodies first; models after. |
| 20 | Model lane scope | **NARROWED** | Three bounded judgments only: kind when evidence is absent, which-amount-is-the-amount once bodies arrive, and per-sender template induction for the tail. Never per-message extraction. |
| 21 | Build our own Drive layer (`openclaw-google-drive-context.md`) | **DEAD** | Drive MCP already does search, full text and OCR. A sweep of 233 files, 22 tier-1 keepers, needed no new credentials. |
| 22 | SENT mail excluded from analysis | **DEAD — was a bug** | Phase 0 did `if SENT in labs: continue`, so self-archived mail was never examined. Self-sent gets its own class with a high prior. |
| 23 | "Did I reply" as the importance signal | **DEAD** | Only 114 addresses written to in 24 months. It misses the entire institutional layer — bank, utility, school, all one-way. |
| 25 | **Derived data lives in state, never in `records/`** | **LIVE** | Production read `senders.csv` from an evidence directory that was gitignored and frozen at 24 months — the ledger could not be rebuilt from a clone and was classifying 18 years with 2 years of rules. Now `~/.local/state/mail-enrichment/`, rebuilt by `mailctl classify`. |
| 26 | **One counterparty identity across stores** | **LIVE** | `ledger.entities` and `life-index.correspondents` are the same concept in different databases. Identity is carried by `entity_key` from `entities.resolve()`; a test asserts the format matches. Without it each store looks correct alone and they never join. |
| 24 | **Performance budget for agent queries** | **LIVE** | Measured 32 ms cold start end-to-end. Budget below; the stdlib-only rule in the query path is what buys it. |

---

## Part 2 — The system

Three stores, three lifecycles, deliberately not one database.

```
                    Gmail (authoritative, read-only)
                              │
                    mail-context.sqlite  ── metadata + snippets, lifetime
                    disposable cache, ~30 min to rebuild
                              │
              ┌───────────────┴───────────────┐
              │                               │
     transactional mail                doc-bearing mail
     (~5/6 of retrieval need)          (~1/6, plus Drive)
              │                               │
        ledger.sqlite                  catalog.sqlite
        entities, transactions          artifacts, fields, text
        WEEKLY use                      YEARLY use
              └───────── shared entity space ─────────┘
                              │
                    Google Drive (via MCP, read-only)
                    search + full text + OCR, already connected
```

**Parsing cascade** — each layer may only terminate or extract, never merely
annotate; decisions are cached per *sender*, not per message:

| Layer | Cost | Effect |
|---|---|---|
| 0 · Gmail listing query | free | junk never fetched (`-category:promotions -category:social`) |
| 1 · sender class | free, precomputed | kills 68% of what remains |
| 2 · structural gate | free | labels, attachment shape |
| 3 · learned sender template | free after first learn | extracts |
| 4 · local model | ~20s, **once per new sender** | learns the template |
| 5 · cloud model | rare | tier-1 documents, lane disagreement |

---

## Part 2b — Performance budget

The OpenClaw agent is chat-triggered; a question from WhatsApp must feel
instant. **Measured on this machine, not estimated:**

| Path | p50 | p95 |
|---|---:|---:|
| MCP cold start: spawn + import + open db + answer a query | **32 ms** | 33 ms |
| ledger query ("what did I pay Acme Lawn") | 0.13 ms | 0.14 ms |
| ledger query (subscriptions, grouped) | 0.04 ms | 0.04 ms |
| mail-context FTS, worst case (prefix widening) | 0.04 ms | 0.06 ms |
| bare `python3` startup, for reference | 7 ms | — |

**The queries are free; process startup is the entire cost**, and 18 ms of the
32 is our code on top of Python's own 14 ms. Data volume is not a factor:
FTS5 cost is proportional to matches, not corpus, so the 4.2× lifetime
expansion should not move these numbers materially.

Budget, and the rules that protect it:

- **< 100 ms** for any agent tool call. Currently 3× under.
- **Never import a heavy library in an MCP path.** Docling, torch or a
  document parser in `lifeindex.mcp` would cost seconds per call and destroy
  the budget. Extraction is offline and out-of-process; the query path stays
  stdlib-only. This is the concrete reason the stdlib rule exists for
  transport and query code (it does not bind offline extraction).
- **Every agent-facing query keeps a covering index.** `transactions` already
  has entity+date, kind+date, counterparty, ref, event.
- **Cap serialised results.** JSON payload, not query time, is the next
  bottleneck; tier-1 text is withheld anyway.
- Re-measure after the lifetime backfill and after the ledger loads real
  volume. Regression = anything over 100 ms.

---

## Part 3 — State of play

**Live and working:** mail-context sync · phase-0 sender classification (1,953
senders) · entity resolver (1,973 addresses → 1,102 entities) · life-index
catalog + CLI + MCP tools (33 tests) · qcbench benchmark harness · Drive MCP.

**Built, never run:** attachment targeting · promo triage list (395 senders) ·
lifetime backfill (`--exclude` added, 140 tests).

**Delivered 2026-08-25 by three agents, all verified:**

- **Drive sweep** — 233 files enumerated, **22 tier-1 keepers**, 32 opened.
  Surfaced three documents the operator had lost track of. Specifics are in the record, not here: plans describe shape,
  records hold the personal detail.
- **Gmail bulk harvest** — selection + resumable harvester + calibration
  report, 227 tests. Independently reproduced the 41/154/498 coverage curve.
- **Ledger prototype — verdict GO.** 3,958-message transactional pool →
  2,374 rows → 1,813 events. **84% precision hand-verified** on 50 random rows
  read against source. 4 templates cover 25% of the pool, 10 cover 50%, 40
  cover 80%. "What did I pay Acme Lawn" returns **19 invoices and the total**
  (68 raw rows before dedup — the collapse is load-bearing, not cosmetic).

**Not started:** ledger in production · ongoing watcher · body fetch.

**Two bugs found by the agents in work I had already handed over:**
`mailctx.targetrun` imported `DB_PATH` from the wrong module and would have
died on line 8 inside the operator's sudo session; and the harvester's first
version recorded every failure, so an expired token mid-run would have marked
the untouched remainder `failed` and skipped it forever. Both fixed, both now
have tests.

**Schema changes required before ingest** (from the Drive sweep): add
`executed` and `superseded_by` to `artifacts` — unsigned drafts sit beside
executed versions and conflating them is worse than not having them. Add
`bill` to the ledger `kind` enum — an invoice was filed as `payment` because
the enum had nowhere to put it.

**Identifier recording policy** (settle once, here, not per sweep): Brazilian
RG/CPF/CNPJ, matrícula, loan, policy, licence and member IDs **are** recorded
— they are the retrieval keys the index exists for. US Social Security numbers
are **not**, in any deliverable or record. Verified held: no SSN-shaped string
appears anywhere in the Drive sweep record.

---

## Part 4 — Sequence

Ordered by value per unit of operator effort. Everything marked ⚑ needs one
interactive sudo session; nothing else needs the operator at all.

1. **Review the Drive queue** — `records/2026-08-24-drive-discovery-sweep/outputs/review-queue.md`.
   22 tier-1 items, keep/drop. No sudo, no code.
2. ⚑ **Sent-mail lifetime backfill** — ~3 min, ~6,200 messages. Seeds the
   people layer, and lifts the sent-to-self blind spot (only 9 in 24 months,
   all with empty subjects, all previously skipped).
3. ⚑ **Body fetch for the transactional pool** — 3,958 ids, best-first TSV
   ready. **This is the binding constraint**, not the tail: it should lift
   amount coverage from 23% toward complete with no template changes.
   *Requires an operator decision: the encryption waiver covers "metadata,
   subjects, and snippets" and does not cover bodies.*
4. ⚑ **Lifetime backfill, junk excluded** — ~1 h. *Includes the systemd
   `--window 0` fix; without it the next scheduled prune deletes ~200k rows.*
5. ⚑ **Targeting run + bulk attachment harvest** — gated: read the real
   candidate count before fetching. Produces the sender-organised calibration
   report.
6. **Operator calibration pass** — edit `sender-verdicts.csv`; ~154 decisions
   cover 80%. Each persists into `sender_rules` and terminates at layer 1.
7. **Ledger into production** + agent tools, within the performance budget.
8. **life-index backfill** — the 20–40 documents that matter, manual, once.
9. *Optional, separate decisions:* encryption migration · draft-with-attachment
   · third-party request handling.

---

## Part 4b — How it gets used

Two rhythms. Confusing them is what produced the rejected digest.

**Ledger — weekly, from the phone.** A question mid-conversation with someone
else. "What did I pay Acme Lawn?" → agent resolves the entity, returns 19
invoices and the total with dates. "What am I subscribed to?" → every
subscription row grouped, twice a year, cancel two. "When were the trip
dates?" → a booking row. **He never files anything**; it is fed from mail that
already arrives. Degraded mode is a low-confidence row or a link to the raw
email — never nothing.

**Catalog — a few times a year, at the machine.** Someone asks for a document.
Drop a scan in `consume/`, or search `escritura <street>`, get the excerpt,
get the path, attach it yourself. Via the agent: "what did we agree to pay for
the apartment" returns **R$ 550.000,00** — the value, not the contract text,
because tier-1 content is withheld from a chat-reachable surface.

The catalog requires a habit, which is why it must stay small: 20–40 documents
that matter, filed once. Email became the life index precisely because it cost
nothing; a filing system that competes with that will lose.

**Where they meet:** the counterparty is shared. *Acme Imóveis* is one entity
whether the evidence is a contract PDF or a payment email.

---

## Part 5 — Open decisions

Only these. Everything else is settled.

| Question | Recommendation |
|---|---|
| **Does the waiver extend to message bodies?** | Explicit operator decision needed before step 3. The waiver names "metadata, subjects, and snippets". |
| Sent-to-self and inbound-only senders | Fixed in principle (#22, #23); needs the lifetime backfill to be worth measuring. |
| Encryption: when? | After the calibration pass, before tier-1 documents accumulate. `bin/li-encrypt` migrates without re-extraction. |
| Should the chat agent read tier-1 *contents*, or only fields and a path? | Fields and path only (current default). The gateway is reachable from WhatsApp. |
| Delete promotional mail already indexed? | No — the unsubscribe triage needs it. Revisit after that runs. |
| Pursue draft-with-attachment at all? | Not until the ledger is in weekly use. |
| Third parties querying the bot (#12/#13) | Design deliberately, after the ledger. It is an exfiltration primitive with a friendly face. |

---

## Where everything lives

| | |
|---|---|
| **This document** | the plan: decisions, architecture, sequence, budget |
| `layers/openclaw/<name>/README.md` | how each component actually works |
| `docs/runbooks/mail-context.md` | how to operate the live index |
| `records/<date>-<slug>/` | what was done, with evidence |
| `docs/plans/archive/` | superseded plans — **do not plan from them** |

`openclaw-google-calendar-context.md` remains live but out of scope here:
calendar is built and connected to nothing.

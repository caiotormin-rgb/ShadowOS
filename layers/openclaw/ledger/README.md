# ledger

The entity ledger: a queryable record of every purchase, subscription, booking,
appointment and payment, keyed on the **counterparty**. Plan:
[`docs/plans/entity-ledger-prd.md`](../../../docs/plans/entity-ledger-prd.md).
Stdlib only, Python 3.12, same idiom as `mail-context`.

Built and validated 2026-08-25 against the metadata snapshot. Findings, the
hand-verified precision numbers and the go/no-go verdict are in
[`records/2026-08-24-ledger-extraction-prototype/`](../../../records/2026-08-24-ledger-extraction-prototype/README.md).
**As of that date this runs on Gmail snippets, not bodies** — 200 characters per
message — which is why 77% of rows have no amount.

```bash
python3 -m unittest discover -s tests            # 72 tests
python3 run_extract.py  <outdir>                 # incremental: snapshot -> CSVs
python3 run_extract.py  <outdir> --full          # reprocess everything
python3 run_extract.py  <outdir> --dry-run       # what would be scanned
python3 build_ledger.py <outdir> <ledger.sqlite> # CSVs -> ledger database
python3 demo_queries.py <ledger.sqlite>          # the PRD's success retrievals
```

`run_extract.py` reads `/tmp/mc-lifetime.sqlite` read-only (`SNAP=` or
`--snapshot` to override) and **exits rather than inventing data** if the
snapshot is missing.

## Scheduled refresh

`refresh.sh` rebuilds the ledger unattended as the account that owns the mail
index (production: `openclaw`), so no run needs sudo: private snapshot of
`~/.local/state/mail-context/mail-context.sqlite` → `senders.py` →
`run_extract.py` (incremental) → `build_ledger.py` → atomic swap of
`~/.local/state/ledger/ledger.sqlite`. It expects `../mail-enrichment` beside
it, as in the repo. A failed run keeps the current ledger, and a build below
80% of the current event count is refused (`MIN_KEEP`), so a pruned index
cannot silently shrink it.

`systemd/ledger-refresh.{service,timer}` run it at 08:15 and 20:15, after the
mail sync, with no network. `station/scripts/refresh-ledger` starts the same
unit on demand. Install and verification:
[`records/2026-09-13-ledger-refresh/`](../../../records/2026-09-13-ledger-refresh/README.md).

## Incremental extraction — the watermark

Extraction is incremental by default. **The watermark is `synced_at`, our own
ingestion clock, never `internal_ts`.** Gmail's `internalDate` is the date on the
message and is not the order we learn about messages: the lifetime backfill
walked *backwards* and added 18 years of mail below any watermark a 24-month run
would have left, and imports and delayed delivery do the same in miniature. A
max-`internal_ts` watermark skips all of that and never notices, because the next
run's watermark is higher still. The message is not late; it is gone.

Selection is a union of two predicates, each catching what the other cannot:

| Predicate | Catches |
|---|---|
| `synced_at >= watermark - overlap` | messages that **changed** — `upsert_message` bumps `synced_at` on every conflict update, so a label edit or resync rewrite is seen |
| `message_id` not in the previous run's outcomes | messages **new to extraction**, whatever any clock says — a stepped clock or an aborted run cannot lose one |

Deletions move neither: `tombstone_message` sets only `deleted_at`, and a hard
prune removes the row. So each run also reconciles against the live id set (66 ms
over 132,588 rows) and drops rows whose message is gone.

The unit of supersession is the **message**, matching
`UNIQUE (message_id, kind, run_id)`: reprocessing a message discards *all* of its
prior rows, so an extractor that stops emitting a spurious row actually removes
it. Each row carries the `run_id` that produced it, and `build_ledger.py` writes
one `extraction_runs` row per run that still owns transactions.

Linking and collapse always run over the **merged** rows, never one run's slice —
an order and the shipping notice that arrives a week later are one event, and
`event_id` therefore spans runs. Run state lives in `<outdir>/extract-state.json`.

Measured on the 132,588-message lifetime snapshot: full run 7.1 s, a 3,317-message
incremental slice 1.1 s, output byte-identical.

## cascade.py — the extraction engine

A layered cascade in which **every layer must either TERMINATE or EXTRACT, and
none may merely annotate.** A layer that only labels leaves the same volume for
the next one, so the funnel never narrows and the costly lane at the bottom pays
for everything.

    L0  sender class          marketing -> drop                    free
    L1  per-sender template   extract, or drop as a known non-event  free, cached
    L2  generic deterministic extract if amount or ref; else drop or escalate
    L3  model lane            not built; the queue is measured

Also here: snippet normalisation (bulk senders pad subjects with zero-width
characters), amount parsing (US, BRL, and Venmo's spaced display form), date
parsing, and event linking.

## templates.py — the unit of cost

**The template key is the sender, never the subject.** Measured on the real
corpus: sender+3-subject-words gives 10,767 families, 82% singletons; sender
alone gives 1,163, and 41 senders cover half the non-marketing corpus. So a
template learned once for `noreply@mytix.njtransit.com` serves all 119 of its
messages, and every future one. Ten templates cover 50% of transactional mail;
forty cover 80%.

Templates are **data** — an ordered rule list per sender — not code paths. They
persist to `extraction_templates` with their rules JSON and sample message ids,
so they are inspectable and reusable rather than re-derived per run. Add a
sender by adding a dict; no engine change.

A `terminate` rule is worth as much as an `extract` rule: it is how volume
actually falls. "a family member requests $140.00" is not money moving.

## Event linking

An order confirmation, its shipping notice and its delivery notice are one
purchase. Rows link by, in descending trustworthiness: `ref_number` (global if
the ref carries a letter and real length, entity-scoped if short and numeric);
quoted item title within a 10-day window anchored on the event's first message
(how Amazon links at all, since it truncates the order number out of most
snippets); and counterparty + normalised date for bookings.

## schema.sql

`entities`, `entity_aliases`, `contacts`, `transactions`, `extraction_runs`,
`extraction_templates`, plus the `ledger` (one row per event) and
`ledger_evidence` (every witnessing message) views. `transactions` is keyed
`UNIQUE (message_id, kind, run_id)`, so a better extractor **supersedes rather
than overwrites**.

`txn_event` and `ledger_evidence` are keyed on `event_id` alone, not
`(run_id, event_id)`: once extraction is incremental, one event's evidence can
come from several runs, and scoping by run would split an order from its
shipping notice.

## Never

- No Gmail calls. This layer reads a local snapshot; body fetch is
  `mail-enrichment/mailharvest.py` and needs an operator session.
- No dependency outside the stdlib in the extraction path.
- Do not edit `mail-enrichment/entities.py` from here. It is validated; this
  layer consumes it and compensates for its known naming gaps in
  `build_ledger.py`.

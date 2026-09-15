#!/usr/bin/env python3
"""Run the cascade over the mail snapshot and emit ledger rows + coverage.

Read-only against the snapshot. Stdlib only.

  python3 run_extract.py <outdir>                  # incremental (default)
  python3 run_extract.py <outdir> --full           # reprocess everything
  python3 run_extract.py <outdir> --since 2026-08-24
  python3 run_extract.py <outdir> --dry-run        # what would be scanned

## The watermark, and why it is `synced_at` and not `internal_ts`

Extraction used to run the cascade over every message on every run. At the
3,958-message prototype pool that was free. The index now holds 132,588 and
grows twice daily, so it is not.

The obvious watermark -- the highest `internal_ts` processed -- is wrong, and
wrong in the worst way: it loses mail silently and permanently.
`internal_ts` is Gmail's internalDate, the date on the *message*, and the order
we learn about messages is not that order:

  * a lifetime backfill walks *backwards*. The 2026-08-25 expansion added 18
    years of mail whose internal_ts is far below any watermark a 24-month run
    would have left behind. Every one of those messages would have been skipped
    forever.
  * imports, IMAP migrations and forwarded archives arrive today carrying old
    dates.
  * delivery is not instantaneous, and Gmail history is explicitly not ordered
    by internalDate.

A max-internal_ts watermark skips all of those, and -- this is what makes it
dangerous rather than merely lossy -- it never notices, because the next run's
watermark is higher still. The message is not late; it is gone.

`synced_at` is stamped by *our own* store when a row is written or refreshed
(`store.upsert_message` sets it on insert and on every conflict update). It is
therefore monotonic in the order we learn things, which is exactly the order a
watermark needs. A late-arriving message is not late in `synced_at`: it gets
today's value whatever its internalDate says, and the next incremental run picks
it up by construction. Same for a message whose labels changed, or that a resync
rewrote.

Two guards on top:

  * **Overlap.** The floor is `watermark - overlap` (default 2 days), because a
    workstation clock can step backwards under NTP and because `synced_at` has
    one-second granularity. Re-scanning a small tail is cheap; a gap is not
    recoverable.
  * **Reconciliation.** Deletions do not bump `synced_at` -- `tombstone_message`
    only sets `deleted_at` -- and a hard prune removes the row entirely. So each
    run also reads the set of live message ids (66 ms on 132,588 rows) and drops
    extracted rows whose message is gone. Reading ids is not reprocessing them;
    the cost that mattered was running the cascade, and that is what the
    watermark avoids.

## Idempotency

Re-running must not double-count and must not strand stale output. The unit of
supersession is the **message**, matching the schema's
`UNIQUE (message_id, kind, run_id)`: when a message is reprocessed, *all* of its
prior rows are discarded and replaced by the new ones. Merging per (message,
kind) instead would leave a spurious row behind forever the day an extractor
stops emitting it.

Each row carries the `run_id` that produced it, so `build_ledger.py` records
which extractor run each transaction came from -- a better extractor supersedes
rather than overwrites, which is what the ledger schema was built for.

Linking and collapse always run over the **merged** row set, never over one
run's rows alone. An order confirmation from last week and its shipping notice
from today belong to one event; if each run linked only its own rows they never
would.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from dataclasses import asdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "mail-enrichment"))

import entities as E                                     # noqa: E402
from cascade import Cascade, Message, Row, link_events, collapse  # noqa: E402
import templates as T                                    # noqa: E402

# The lifetime snapshot, matching `mailctl`'s own SNAP default. The 24-month
# /tmp/mc-snap.sqlite this used to point at was frozen at 3,958 transactional
# messages while the index grew to 132,588.
SNAP = os.environ.get("SNAP", "/tmp/mc-lifetime.sqlite")
# Sender classification is DERIVED, not archived. It used to be read from a
# records/ evidence directory — gitignored, and frozen at the 24-month corpus
# while the index grew to 18 years. Rebuild it with:
#   python3 ~/workspace/layers/openclaw/mail-enrichment/senders.py <snapshot>
PHASE0 = os.environ.get(
    "SENDERS_CSV",
    os.path.expanduser("~/.local/state/mail-enrichment/senders.csv"))

STATE_FILE = "extract-state.json"
RAW_CSV = "transactions-raw.csv"
EVENT_CSV = "transactions.csv"
OUTCOME_CSV = "cascade-outcomes.csv"
DEFAULT_OVERLAP_DAYS = 2

COLS = ["entity_key", "entity_name", "counterparty", "date", "kind", "amount",
        "currency", "ref_number", "service_dates", "message_id", "confidence",
        "lane", "rule", "description", "link_key", "link_basis", "event_id",
        "run_id"]
OUTCOME_COLS = ["sender", "action", "layer", "reason", "message_id", "run_id"]


# ------------------------------------------------------------------ state

def load_state(outdir: str) -> dict:
    path = os.path.join(outdir, STATE_FILE)
    if not os.path.exists(path):
        return {"watermark_synced_at": 0, "last_run_id": 0, "runs": []}
    with open(path) as fh:
        return json.load(fh)


def save_state(outdir: str, state: dict) -> None:
    with open(os.path.join(outdir, STATE_FILE), "w") as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")


def parse_since(raw: str) -> int:
    """A YYYY-MM-DD date or a bare epoch, as a `synced_at` floor.

    `--since` overrides the watermark, and the watermark is in *ingestion* time,
    not message time -- so this means "everything the index learned since then",
    which is the thing that is safe to compose with an incremental run. Filtering
    by message date instead would reintroduce exactly the internal_ts bug the
    watermark exists to avoid.
    """
    raw = raw.strip()
    if raw.isdigit() and len(raw) >= 9:
        return int(raw)
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m"):
        try:
            return int(time.mktime(time.strptime(raw, fmt)))
        except ValueError:
            continue
    raise SystemExit(f"--since: cannot read {raw!r} as a date or an epoch")


# ------------------------------------------------------------------ prior output

def _f(v):
    return float(v) if v not in ("", None) else None


def row_from_csv(d: dict) -> Row:
    return Row(
        entity_key=d["entity_key"], entity_name=d["entity_name"],
        counterparty=d["counterparty"], date=d["date"], kind=d["kind"],
        amount=_f(d["amount"]), currency=d["currency"] or None,
        ref_number=d["ref_number"] or None,
        service_dates=d["service_dates"] or None,
        message_id=d["message_id"], confidence=_f(d["confidence"]) or 0.0,
        lane=d["lane"], rule=d["rule"], description=d["description"] or None,
        link_key=d["link_key"] or None, link_basis=d["link_basis"] or None,
        event_id=int(d["event_id"]) if d.get("event_id") else None)


def read_prior(outdir: str) -> tuple[dict[str, list[Row]], dict[str, tuple],
                                     dict[str, int]]:
    """Rows and outcomes from the last run, keyed by message_id."""
    rows: dict[str, list[Row]] = {}
    outcomes: dict[str, tuple] = {}
    run_of: dict[str, int] = {}
    raw = os.path.join(outdir, RAW_CSV)
    if os.path.exists(raw):
        with open(raw, newline="") as fh:
            for d in csv.DictReader(fh):
                rows.setdefault(d["message_id"], []).append(row_from_csv(d))
                run_of[d["message_id"]] = int(d.get("run_id") or 1)
    oc = os.path.join(outdir, OUTCOME_CSV)
    if os.path.exists(oc):
        with open(oc, newline="") as fh:
            for d in csv.DictReader(fh):
                outcomes[d["message_id"]] = (d["sender"], d["action"], d["layer"],
                                             d["reason"], d["message_id"])
                run_of.setdefault(d["message_id"], int(d.get("run_id") or 1))
    return rows, outcomes, run_of


# ------------------------------------------------------------------ corpus

def open_snapshot(path: str) -> sqlite3.Connection:
    if not os.path.exists(path):
        sys.exit(f"FATAL: snapshot {path} is gone. Refusing to invent data.")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


SELECT_COLS = ("select m.message_id, m.thread_id, m.from_addr, m.subject, "
               "m.snippet, m.internal_ts from mail_messages m")


def load_corpus(db, floor: int | None, seen: set[str]):
    """(classes, entities, addr->key, messages-to-process, live ids, max synced_at).

    Entity resolution reads sender *counts* over the whole corpus even on an
    incremental run: an entity's identity must not change depending on how much
    of the mailbox happened to be scanned this time. That is a cheap GROUP BY,
    not a cascade pass.

    Selection is a UNION of two predicates, and each catches what the other
    cannot:

      * `synced_at >= floor` catches messages that CHANGED -- labels edited, a
        resync rewrite -- because `upsert_message` bumps `synced_at` on every
        conflict update. An id-set check would never see those; the row was
        already processed once.
      * `message_id not in seen` catches messages that are NEW to extraction,
        whatever their timestamps say. This is the belt to the timestamp's
        braces: if the clock ever steps backwards, or a run aborts after moving
        the watermark, or a message lands with a `synced_at` below the floor for
        any reason at all, it is still picked up. A message can be late; it
        cannot be lost.

    `seen` is every message the previous run recorded a cascade verdict for --
    extract, terminate or escalate alike -- not just the ones that produced rows.
    """
    with open(PHASE0, newline="") as fh:
        classes = {r["sender"].lower(): r["class"] for r in csv.DictReader(fh)}
    counts = db.execute(
        "select from_addr, count(*) from mail_messages "
        "where deleted_at is null group by 1").fetchall()
    ents = E.build(counts, classes)
    a2k = {a: k for k, e in ents.items() for a in e.addresses}

    live_rows = db.execute(
        "select message_id, synced_at from mail_messages where deleted_at is null"
    ).fetchall()
    live = {mid for mid, _ in live_rows}
    high = db.execute(
        "select coalesce(max(synced_at), 0) from mail_messages").fetchone()[0]

    if floor is None:
        msgs = db.execute(SELECT_COLS + " where m.deleted_at is null "
                                        "order by m.internal_ts").fetchall()
        return classes, ents, a2k, msgs, live, high

    todo = [mid for mid, sat in live_rows if sat >= floor or mid not in seen]
    # A TEMP table rather than a chunked IN (...): SQLite caps bound variables
    # at 999, and the join keeps this one statement whatever the batch size.
    # TEMP objects live outside the read-only main database, so this is legal
    # against a snapshot opened mode=ro.
    db.execute("CREATE TEMP TABLE _todo (message_id TEXT PRIMARY KEY)")
    db.executemany("INSERT OR IGNORE INTO _todo VALUES (?)", ((m,) for m in todo))
    msgs = db.execute(
        SELECT_COLS + " join _todo t on t.message_id = m.message_id "
                      "where m.deleted_at is null order by m.internal_ts").fetchall()
    return classes, ents, a2k, msgs, live, high


# ------------------------------------------------------------------ main

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_extract.py",
        description="Extract ledger rows from the mail snapshot.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Default is incremental: only messages the index has written or "
               "refreshed since the last run are put through the cascade. The "
               "watermark is on synced_at (our ingestion clock), never on "
               "internal_ts (Gmail's message date) -- see the module docstring.")
    p.add_argument("outdir", nargs="?", default=os.path.join(HERE, "out"))
    p.add_argument("--full", action="store_true",
                   help="reprocess the whole corpus and rewrite the outputs")
    p.add_argument("--since", metavar="WHEN",
                   help="override the watermark: YYYY-MM-DD or epoch, as a "
                        "synced_at (ingestion time) floor")
    p.add_argument("--overlap-days", type=float, default=DEFAULT_OVERLAP_DAYS,
                   help=f"re-scan window below the watermark (default {DEFAULT_OVERLAP_DAYS})")
    p.add_argument("--snapshot", default=SNAP, help=f"mail snapshot (default {SNAP})")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would be scanned, write nothing")
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    outdir = a.outdir
    os.makedirs(outdir, exist_ok=True)
    state = load_state(outdir)
    prior_rows, prior_outcomes, run_of = read_prior(outdir)

    if a.full:
        mode, floor = "full", None
    elif a.since:
        mode, floor = "since", parse_since(a.since)
    elif state.get("watermark_synced_at"):
        mode = "incremental"
        floor = max(0, int(state["watermark_synced_at"])
                    - int(a.overlap_days * 86400))
    else:
        # No watermark yet: the first run is necessarily a full one, and says so
        # rather than quietly producing a partial corpus.
        mode, floor = "first-run(full)", None

    db = open_snapshot(a.snapshot)
    classes, ents, a2k, msgs, live, high = load_corpus(
        db, floor, set(prior_outcomes))

    if a.dry_run:
        print(json.dumps({
            "mode": mode, "snapshot": a.snapshot, "floor_synced_at": floor,
            "watermark_synced_at": state.get("watermark_synced_at", 0),
            "messages_to_scan": len(msgs), "messages_live": len(live),
            "messages_already_seen": len(prior_outcomes),
            "prior_rows": sum(len(v) for v in prior_rows.values()),
        }, indent=2))
        db.close()
        return 0

    run_id = int(state.get("last_run_id", 0)) + 1
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tpl = T.load()

    def resolve(addr):
        k = a2k.get(addr)
        return (k, ents[k].best_name()) if k else ("unknown", addr)

    casc = Cascade(tpl, classes, resolve)
    db.close()          # everything needed is in memory; do not hold the snapshot
    scanned = 0
    for mid, tid, fa, su, sn, ts in msgs:
        addr = E.address_of(fa)
        if not addr:
            continue
        scanned += 1
        o = casc.run(Message(mid, fa, su or "", sn or "", ts, tid), addr)
        # Message-granular supersession: this run's verdict replaces whatever
        # the message produced before, including "it produced nothing".
        prior_rows[mid] = list(o.rows)
        prior_outcomes[mid] = (addr, o.action, o.layer, o.reason, mid)
        run_of[mid] = run_id

    # Reconcile: a message deleted in Gmail or pruned from the index must not
    # keep its extracted rows. Tombstones do not move synced_at, so the
    # watermark cannot see them; this can.
    dropped = [mid for mid in list(prior_rows) if mid not in live]
    for mid in dropped:
        prior_rows.pop(mid, None)
    for mid in [m for m in list(prior_outcomes) if m not in live]:
        prior_outcomes.pop(mid, None)

    rows = [r for mid in prior_rows for r in prior_rows[mid]]
    # Canonical order before linking, so the output of a run does not depend on
    # the order rows happened to be merged in. link_events' sort is stable, so
    # this is what makes two runs over the same corpus byte-identical.
    rows.sort(key=lambda r: (r.date, r.message_id, r.kind, r.rule or ""))
    link_events(rows)
    events = collapse(rows)
    outcomes = sorted(prior_outcomes.values(), key=lambda o: o[4])

    for name, data in ((RAW_CSV, rows), (EVENT_CSV, events)):
        with open(os.path.join(outdir, name), "w", newline="") as fh:
            w = csv.DictWriter(fh, COLS, extrasaction="ignore")
            w.writeheader()
            for r in data:
                w.writerow({**asdict(r), "run_id": run_of.get(r.message_id, run_id)})
    with open(os.path.join(outdir, OUTCOME_CSV), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(OUTCOME_COLS)
        for o in outcomes:
            w.writerow([*o, run_of.get(o[4], run_id)])

    summary = coverage(outcomes, rows, events, tpl, casc, mode, floor, run_id,
                       scanned, len(dropped), started, a.snapshot)
    with open(os.path.join(outdir, "coverage.json"), "w") as fh:
        json.dump(summary, fh, indent=2)
    print(json.dumps(summary, indent=2))

    write_escalations(outdir, outcomes, ents, a2k)

    state["watermark_synced_at"] = int(high)
    state["last_run_id"] = run_id
    state["snapshot"] = a.snapshot
    state.setdefault("runs", []).append({
        "run_id": run_id, "mode": mode, "started_at": started,
        "floor_synced_at": floor, "messages_scanned": scanned,
        "messages_dropped_as_deleted": len(dropped),
        "rows_total": len(rows), "watermark_synced_at": int(high),
    })
    state["runs"] = state["runs"][-50:]
    save_state(outdir, state)
    return 0


# ------------------------------------------------------------------ coverage

def coverage(outcomes, rows, events, tpl, casc, mode, floor, run_id, scanned,
             dropped, started, snapshot) -> dict:
    """The product metric. Corpus-wide numbers describe the merged output, not
    this run's slice -- an incremental run that touched 56 messages must not
    report that the corpus is 56 messages."""
    per_sender = Counter(o[0] for o in outcomes)
    nonmkt = [o for o in outcomes if o[2] != "L0"]
    acted = [o for o in nonmkt if o[1] == "extract"]
    esc = [o for o in nonmkt if o[1] == "escalate"]
    tpl_senders = set(tpl)
    nonmkt_msgs = len(nonmkt) or 1

    # One pass. The previous shape of this (a comprehension per sender over all
    # outcomes) was fine at 3,958 messages and quadratic at 132,588.
    nonmkt_by_sender = Counter(o[0] for o in nonmkt)
    cum, curve = 0, {}
    for i, (s, n) in enumerate(nonmkt_by_sender.most_common(), 1):
        cum += n
        for target in (50, 80, 90):
            if target not in curve and cum / nonmkt_msgs >= target / 100:
                curve[target] = i

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run": {
            "run_id": run_id, "mode": mode, "started_at": started,
            "snapshot": snapshot,
            "floor_synced_at": floor,
            "messages_scanned_this_run": scanned,
            "rows_dropped_as_deleted": dropped,
            "cascade_this_run": dict(sorted(casc.stats.items())),
        },
        "messages_total": len(outcomes),
        "messages_non_marketing": len(nonmkt),
        "messages_extracted": len(acted),
        "senders_total": len(per_sender),
        "senders_non_marketing": len(nonmkt_by_sender),
        "senders_with_template": len(tpl_senders),
        "messages_from_templated_senders":
            sum(per_sender[s] for s in tpl_senders),
        "pct_non_marketing_msgs_from_templated_senders":
            round(100 * sum(per_sender[s] for s in tpl_senders) / nonmkt_msgs, 1),
        "rows_extracted": len(rows),
        "rows_L1": sum(1 for r in rows if r.lane == "L1"),
        "rows_L2": sum(1 for r in rows if r.lane == "L2"),
        "events_after_linking": len(events),
        "escalated": len(esc),
        "senders_to_reach_pct_of_non_marketing": curve,
        "link_basis": dict(Counter(r.link_basis for r in rows)),
        "rows_with_amount": sum(1 for r in rows if r.amount is not None),
        "rows_with_ref": sum(1 for r in rows if r.ref_number),
        "kinds": dict(Counter(r.kind for r in rows)),
    }


def write_escalations(outdir, outcomes, ents, a2k) -> None:
    esc = [o for o in outcomes if o[2] != "L0" and o[1] == "escalate"]
    q = Counter(o[0] for o in esc)
    reasons = {o[0]: o[3] for o in esc}
    with open(os.path.join(outdir, "escalation-queue.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sender", "messages", "entity", "example_reason"])
        for s, n in q.most_common():
            k = a2k.get(s, "?")
            w.writerow([s, n, ents[k].best_name() if k in ents else "?",
                        reasons.get(s, "")])


if __name__ == "__main__":
    sys.exit(main())

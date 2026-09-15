"""Incremental extraction: the watermark, and the message that must not be lost.

Extraction used to run the cascade over the whole corpus every time. These tests
pin the replacement, and in particular the property the obvious implementation
does not have: **a message whose internalDate is older than everything already
processed must still be picked up.** Gmail history is not ordered by
internalDate, and the 2026-08-25 lifetime backfill added 18 years of mail below
any watermark a 24-month run would have left.

Fixtures are synthetic. A real snapshot is 157 MB of personal mail and must not
be a test dependency. Timestamps are at real epoch scale so the default overlap
window is exercised rather than swamped.

    python3 -m unittest discover -s layers/openclaw/ledger/tests
"""
from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "..", "mail-enrichment"))

import run_extract as RX                                          # noqa: E402

MC_SCHEMA = os.path.join(ROOT, "..", "mail-context", "schema.sql")

# A sender with a template in templates.py would couple these tests to that
# file. The generic L2 lane is enough to make rows appear, and what is under
# test is selection, not extraction.
SENDER = "billing@storefront.invalid"
NOW = 1_780_000_000          # ingestion clock
DAY = 86_400
OLD = 1_100_000_000          # 2004, far below anything else here


class Corpus:
    """A throwaway snapshot plus an output directory."""

    def __init__(self, stack: contextlib.ExitStack):
        tmp = stack.enter_context(tempfile.TemporaryDirectory())
        self.out = os.path.join(tmp, "out")
        self.snap = os.path.join(tmp, "snap.sqlite")
        db = sqlite3.connect(self.snap)
        with open(MC_SCHEMA) as fh:
            db.executescript(fh.read())
        db.commit()
        db.close()
        self.senders_csv = os.path.join(tmp, "senders.csv")
        with open(self.senders_csv, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["sender", "class"])
            w.writerow([SENDER, "transactional"])

    # -- snapshot ---------------------------------------------------------
    def put(self, mid, *, internal_ts, synced_at, ref="AB10001"):
        db = sqlite3.connect(self.snap)
        db.execute("INSERT OR IGNORE INTO mail_threads (thread_id, message_count, synced_at)"
                   " VALUES (?, 1, ?)", (f"t-{mid}", synced_at))
        db.execute("""INSERT INTO mail_messages (message_id, thread_id, internal_ts,
                          synced_at, from_addr, subject, snippet)
                      VALUES (?,?,?,?,?,?,?)
                      ON CONFLICT(message_id) DO UPDATE SET
                        internal_ts=excluded.internal_ts, synced_at=excluded.synced_at,
                        subject=excluded.subject, snippet=excluded.snippet""",
                   (mid, f"t-{mid}", internal_ts, synced_at, SENDER,
                    f"Order {ref}",
                    f"Your order #{ref} total $12.00 has shipped"))
        db.commit()
        db.close()

    def tombstone(self, mid, at=NOW):
        db = sqlite3.connect(self.snap)
        # Exactly what store.tombstone_message does: deleted_at only. It
        # deliberately does NOT touch synced_at, which is why the watermark
        # alone cannot see deletions.
        db.execute("UPDATE mail_messages SET deleted_at=? WHERE message_id=?", (at, mid))
        db.commit()
        db.close()

    def hard_delete(self, mid):
        db = sqlite3.connect(self.snap)
        db.execute("DELETE FROM mail_messages WHERE message_id=?", (mid,))
        db.commit()
        db.close()

    # -- runner -----------------------------------------------------------
    def run(self, *args):
        RX.PHASE0 = self.senders_csv
        with contextlib.redirect_stdout(io.StringIO()):
            rc = RX.main([self.out, "--snapshot", self.snap, *args])
        assert rc == 0
        with open(os.path.join(self.out, "coverage.json")) as fh:
            return json.load(fh)

    def dry(self, *args):
        RX.PHASE0 = self.senders_csv
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            RX.main([self.out, "--snapshot", self.snap, "--dry-run", *args])
        return json.loads(buf.getvalue())

    # -- output -----------------------------------------------------------
    def state(self):
        with open(os.path.join(self.out, RX.STATE_FILE)) as fh:
            return json.load(fh)

    def raw_rows(self):
        with open(os.path.join(self.out, RX.RAW_CSV), newline="") as fh:
            return list(csv.DictReader(fh))

    def by_msg(self):
        return {r["message_id"]: r for r in self.raw_rows()}

    def outcome_ids(self):
        with open(os.path.join(self.out, RX.OUTCOME_CSV), newline="") as fh:
            return {r["message_id"] for r in csv.DictReader(fh)}

    def content(self):
        """Every column except run_id, which is a provenance stamp and is meant
        to move when a message is genuinely reprocessed."""
        return [{k: v for k, v in r.items() if k != "run_id"} for r in self.raw_rows()]


class Base(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.c = Corpus(self.stack)

    def another(self) -> Corpus:
        return Corpus(self.stack)


class WatermarkTest(Base):
    def test_first_run_is_full_and_records_a_watermark(self):
        self.c.put("m1", internal_ts=NOW - 10 * DAY, synced_at=NOW - DAY)
        self.c.put("m2", internal_ts=NOW - 9 * DAY, synced_at=NOW, ref="AB10002")
        cov = self.c.run()
        self.assertEqual(cov["run"]["mode"], "first-run(full)")
        self.assertEqual(cov["run"]["messages_scanned_this_run"], 2)
        self.assertEqual(self.c.state()["watermark_synced_at"], NOW)

    def test_incremental_run_scans_only_what_changed(self):
        for i in range(6):
            self.c.put(f"m{i}", internal_ts=NOW - (90 - i * 10) * DAY,
                       synced_at=NOW - (90 - i * 10) * DAY, ref=f"AB1000{i}")
        self.c.run()
        self.c.put("new", internal_ts=NOW, synced_at=NOW, ref="AB20000")
        cov = self.c.run()
        self.assertEqual(cov["run"]["mode"], "incremental")
        # Two, not one: the new message, plus the message that sat exactly at
        # the old watermark. The floor is inclusive on purpose -- excluding it
        # would open a one-second hole every run.
        self.assertEqual(cov["run"]["messages_scanned_this_run"], 2)
        self.assertEqual(len(self.c.outcome_ids()), 7,
                         "the merged output still describes the whole corpus")

    def test_the_overlap_window_rescans_the_tail(self):
        """Deliberate cheap redundancy: a clock that steps back a few hours must
        not open a gap. Re-scanning a tail is recoverable; a gap is not."""
        self.c.put("m1", internal_ts=NOW - DAY, synced_at=NOW - DAY)
        self.c.run()
        got = self.c.dry()
        self.assertEqual(got["floor_synced_at"], NOW - DAY - 2 * DAY)
        self.assertEqual(got["messages_to_scan"], 1)
        # ...and it is tunable when the tail is expensive.
        self.assertEqual(self.c.dry("--overlap-days", "0")["messages_to_scan"], 1)

    def test_full_forces_a_complete_reprocess(self):
        self.c.put("m1", internal_ts=NOW - 90 * DAY, synced_at=NOW - 90 * DAY)
        self.c.put("m2", internal_ts=NOW, synced_at=NOW, ref="AB10002")
        self.c.run()
        cov = self.c.run("--full")
        self.assertEqual(cov["run"]["mode"], "full")
        self.assertEqual(cov["run"]["messages_scanned_this_run"], 2)

    def test_since_overrides_the_watermark(self):
        self.c.put("m1", internal_ts=NOW - 90 * DAY, synced_at=NOW - 90 * DAY)
        self.c.put("m2", internal_ts=NOW, synced_at=NOW, ref="AB10002")
        self.c.run()
        got = self.c.dry("--since", "1000000000")
        self.assertEqual(got["mode"], "since")
        self.assertEqual(got["messages_to_scan"], 2)

    def test_since_accepts_a_date_and_an_epoch(self):
        self.assertEqual(RX.parse_since("1787600000"), 1787600000)
        self.assertGreater(RX.parse_since("2026-08-24"), 1_780_000_000)
        with self.assertRaises(SystemExit):
            RX.parse_since("last tuesday")


class LateArrivalTest(Base):
    """The property a max(internal_ts) watermark does not have."""

    def test_a_message_with_an_older_internal_ts_is_still_picked_up(self):
        self.c.put("recent1", internal_ts=NOW - DAY, synced_at=NOW - 95 * DAY)
        self.c.put("recent2", internal_ts=NOW, synced_at=NOW - 90 * DAY, ref="AB10002")
        self.c.run()

        # Now a 2004 message lands: an import, a backfill walking backwards, a
        # delayed delivery. Its internal_ts is far BELOW the highest already
        # processed; its synced_at is today's.
        self.c.put("ancient", internal_ts=OLD, synced_at=NOW, ref="AB10003")
        cov = self.c.run()

        self.assertEqual(cov["run"]["messages_scanned_this_run"], 2,
                         "the newcomer plus the row at the watermark boundary")
        self.assertIn("ancient", self.c.outcome_ids(),
                      "a max-internal_ts watermark would have skipped this "
                      "message on this run and on every run after it")
        self.assertIn("ancient", self.c.by_msg())

    def test_a_message_below_the_synced_at_floor_is_still_picked_up(self):
        """The belt to the timestamp's braces.

        If the clock steps backwards, or a run aborts after moving the
        watermark, a genuinely new message can carry a synced_at BELOW the
        floor. The id set catches it anyway: 'never extracted' does not depend
        on any clock.
        """
        self.c.put("m1", internal_ts=NOW - DAY, synced_at=NOW)
        self.c.run()
        self.assertEqual(self.c.state()["watermark_synced_at"], NOW)

        self.c.put("straggler", internal_ts=NOW - DAY, synced_at=NOW - 400 * DAY,
                   ref="AB10002")
        cov = self.c.run()
        self.assertEqual(cov["run"]["mode"], "incremental")
        self.assertIn("straggler", self.c.outcome_ids(),
                      "its synced_at is 400 days below the floor; only the "
                      "id set could have found it")
        self.assertEqual(cov["run"]["messages_scanned_this_run"], 2)

    def test_an_edited_message_is_reprocessed(self):
        """synced_at bumps on every upsert conflict, so a changed message is
        caught by the floor even though its id is already in the seen set."""
        self.c.put("m1", internal_ts=NOW - 90 * DAY, synced_at=NOW - 90 * DAY)
        self.c.run()
        self.assertEqual(self.c.by_msg()["m1"]["ref_number"], "AB10001")
        self.c.put("m1", internal_ts=NOW - 90 * DAY, synced_at=NOW, ref="AB99999")
        cov = self.c.run()
        self.assertEqual(cov["run"]["messages_scanned_this_run"], 1)
        self.assertEqual(self.c.by_msg()["m1"]["ref_number"], "AB99999")

    def test_a_message_that_stops_producing_rows_loses_them(self):
        """Message-granular supersession. Merging per (message, kind) would
        leave a row behind forever the day an extractor stops emitting it."""
        self.c.put("m1", internal_ts=NOW - 90 * DAY, synced_at=NOW - 90 * DAY)
        self.c.run()
        self.assertIn("m1", self.c.by_msg())
        # Rewrite it as something the cascade terminates on.
        db = sqlite3.connect(self.c.snap)
        db.execute("UPDATE mail_messages SET subject='Hello', snippet='just saying hi',"
                   " synced_at=? WHERE message_id='m1'", (NOW,))
        db.commit()
        db.close()
        self.c.run()
        self.assertNotIn("m1", self.c.by_msg())
        self.assertIn("m1", self.c.outcome_ids(), "still seen, just not extracted")


class IdempotencyTest(Base):
    def test_rerunning_changes_nothing(self):
        for i in range(6):
            self.c.put(f"m{i}", internal_ts=NOW - (50 - i) * DAY,
                       synced_at=NOW - (50 - i) * DAY, ref=f"AB1000{i}")
        self.c.run()
        first = self.c.content()
        self.c.run()
        self.c.run("--full")
        self.assertEqual(self.c.content(), first)

    def test_output_does_not_depend_on_how_it_was_produced(self):
        """One full run and a sequence of incremental runs must converge on the
        same output -- otherwise the watermark silently changes the answer."""
        msgs = [(f"m{i}", NOW - (50 - i) * DAY, NOW - (50 - i) * DAY, f"AB1000{i}")
                for i in range(8)]
        for mid, its, sat, ref in msgs:
            self.c.put(mid, internal_ts=its, synced_at=sat, ref=ref)
        self.c.run("--full")
        all_at_once = self.c.content()

        step = self.another()
        for mid, its, sat, ref in msgs:
            step.put(mid, internal_ts=its, synced_at=sat, ref=ref)
            step.run()
        self.assertEqual(step.content(), all_at_once)

    def test_event_ids_are_stable_across_run_shapes(self):
        for i in range(5):
            self.c.put(f"m{i}", internal_ts=NOW - (50 - i) * DAY,
                       synced_at=NOW - (50 - i) * DAY, ref=f"AB1000{i}")
        self.c.run()
        before = {m: r["event_id"] for m, r in self.c.by_msg().items()}
        self.c.run("--full")
        self.assertEqual({m: r["event_id"] for m, r in self.c.by_msg().items()}, before)

    def test_coverage_describes_the_corpus_not_the_slice(self):
        for i in range(4):
            self.c.put(f"m{i}", internal_ts=NOW - (50 - i * 10) * DAY,
                       synced_at=NOW - (50 - i * 10) * DAY, ref=f"AB1000{i}")
        self.c.run()
        self.c.put("new", internal_ts=NOW, synced_at=NOW, ref="AB20000")
        cov = self.c.run()
        self.assertEqual(cov["run"]["messages_scanned_this_run"], 2)
        self.assertEqual(cov["messages_total"], 5,
                         "an incremental run must not report a 1-message corpus")
        self.assertEqual(cov["rows_extracted"], 5)


class ReconciliationTest(Base):
    def test_a_tombstoned_message_loses_its_rows(self):
        self.c.put("m1", internal_ts=NOW - 90 * DAY, synced_at=NOW - 90 * DAY)
        self.c.put("m2", internal_ts=NOW - 89 * DAY, synced_at=NOW - 89 * DAY,
                   ref="AB10002")
        self.c.run()
        self.assertEqual(len(self.c.outcome_ids()), 2)

        self.c.tombstone("m2")
        cov = self.c.run()
        # The tombstone did not move synced_at, so the watermark cannot see it.
        # Reconciliation against the live id set can.
        self.assertEqual(cov["run"]["rows_dropped_as_deleted"], 1)
        self.assertEqual(self.c.outcome_ids(), {"m1"})
        self.assertNotIn("m2", self.c.by_msg())

    def test_a_hard_pruned_message_loses_its_rows(self):
        self.c.put("m1", internal_ts=NOW - 90 * DAY, synced_at=NOW - 90 * DAY)
        self.c.put("m2", internal_ts=NOW - 89 * DAY, synced_at=NOW - 89 * DAY,
                   ref="AB10002")
        self.c.run()
        self.c.hard_delete("m2")
        self.c.run()
        self.assertEqual(self.c.outcome_ids(), {"m1"})


class LinkingTest(Base):
    def test_linking_spans_runs(self):
        """An order and its shipping notice arriving in different runs are one
        event. Linking runs over the merged rows, never over one run's slice."""
        self.c.put("order", internal_ts=NOW - 40 * DAY, synced_at=NOW - 400 * DAY,
                   ref="AB77777")
        # A newer message in the same run pushes the watermark past `order`, so
        # run 2 does not rescan it and the two rows keep different run ids.
        self.c.put("filler", internal_ts=NOW - 40 * DAY, synced_at=NOW - 300 * DAY,
                   ref="AB88888")
        self.c.run()
        self.c.put("ship", internal_ts=NOW - 39 * DAY, synced_at=NOW, ref="AB77777")
        self.c.run()
        rows = self.c.by_msg()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows["order"]["event_id"], rows["ship"]["event_id"],
                         "same ref number, one event, produced by two runs")
        self.assertNotEqual(rows["order"]["run_id"], rows["ship"]["run_id"])


class StateTest(Base):
    def test_state_records_every_run(self):
        self.c.put("m1", internal_ts=NOW - 90 * DAY, synced_at=NOW - 90 * DAY)
        self.c.run()
        self.c.put("m2", internal_ts=NOW, synced_at=NOW, ref="AB10002")
        self.c.run()
        runs = self.c.state()["runs"]
        self.assertEqual([r["run_id"] for r in runs], [1, 2])
        self.assertEqual([r["mode"] for r in runs], ["first-run(full)", "incremental"])

    def test_dry_run_writes_nothing(self):
        self.c.put("m1", internal_ts=NOW - DAY, synced_at=NOW)
        got = self.c.dry()
        self.assertEqual(got["messages_to_scan"], 1)
        self.assertFalse(os.path.exists(os.path.join(self.c.out, RX.RAW_CSV)))
        self.assertFalse(os.path.exists(os.path.join(self.c.out, RX.STATE_FILE)))

    def test_missing_snapshot_refuses_rather_than_inventing_data(self):
        with self.assertRaises(SystemExit):
            RX.main([self.c.out, "--snapshot", "/nonexistent/snap.sqlite"])


if __name__ == "__main__":
    unittest.main()

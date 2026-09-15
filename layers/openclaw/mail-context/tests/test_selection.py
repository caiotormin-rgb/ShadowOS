"""Selection: the layer-1/layer-2 cascade that decides what gets fetched.

The invariants worth defending are the ones that cost real money when broken:
a terminated sender must never reach the API, an operator decision must beat a
heuristic class, and the fetch order must put the good stuff first so a run cut
short is still worth something.
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from mailctx import selection


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript((Path(__file__).resolve().parent.parent / "schema.sql").read_text())
    return conn


class Base(unittest.TestCase):
    def setUp(self):
        self.conn = _conn()

    def msg(self, mid, *, sender, ts=1000, subject="s", labels=(), hints=(),
            deleted=None):
        self.conn.execute(
            "INSERT OR IGNORE INTO mail_threads (thread_id, message_count, synced_at)"
            " VALUES (?,1,0)", (f"t-{mid}",))
        self.conn.execute(
            """INSERT INTO mail_messages (message_id, thread_id, internal_ts, synced_at,
                   from_addr, subject, deleted_at) VALUES (?,?,?,0,?,?,?)""",
            (mid, f"t-{mid}", ts, sender, subject, deleted))
        for l in labels:
            self.conn.execute(
                "INSERT INTO mail_labels (message_id, label) VALUES (?,?)", (mid, l))
        for h in hints:
            self.conn.execute(
                """INSERT INTO mail_attachment_hints (message_id, hint, synced_at)
                   VALUES (?,?,0)""", (mid, h))
        self.conn.commit()


class NormalizeTest(unittest.TestCase):
    def test_display_name_is_stripped(self):
        self.assertEqual(
            selection.normalize_addr('"Acme Imoveis" <contato@acme-imoveis.com.br>'),
            "contato@acme-imoveis.com.br")

    def test_bare_address_survives_and_lowercases(self):
        self.assertEqual(selection.normalize_addr("A@B.COM"), "a@b.com")

    def test_empty_is_empty_not_none(self):
        self.assertEqual(selection.normalize_addr(None), "")


class CascadeTest(Base):
    def test_marketing_is_terminated_by_the_class_layer(self):
        self.msg("m1", sender="promo@shop.com", hints=("pdf",))
        self.msg("m2", sender="sam@school.org", hints=("pdf",))
        got = selection.select_candidates(
            self.conn, classes={"promo@shop.com": "marketing",
                                "sam@school.org": "human"})
        self.assertEqual([c["message_id"] for c in got], ["m2"])

    def test_image_only_messages_are_not_candidates(self):
        """Layer 2: 'has an attachment' is not 'has a document'."""
        self.msg("m1", sender="a@x.com", hints=("any", "image"))
        self.msg("m2", sender="a@x.com", hints=("any", "pdf"))
        got = selection.select_candidates(self.conn)
        self.assertEqual([c["message_id"] for c in got], ["m2"])

    def test_unknown_class_is_kept_on_the_wide_pass(self):
        """'unknown' is 13.6% of the corpus and is exactly the population the
        calibration run exists to judge. Excluding it defeats the purpose."""
        self.msg("m1", sender="mystery@x.com", hints=("pdf",))
        got = selection.select_candidates(self.conn, classes={})
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["sender_class"], "unknown")

    def test_deleted_messages_are_excluded(self):
        self.msg("m1", sender="a@x.com", hints=("pdf",), deleted=123)
        self.assertEqual(selection.select_candidates(self.conn), [])


class VerdictTest(Base):
    def test_never_verdict_beats_a_kept_class(self):
        self.msg("m1", sender="noreply@bank.com", hints=("pdf",))
        classes = {"noreply@bank.com": "service-transactional"}
        self.assertEqual(len(selection.select_candidates(self.conn, classes=classes)), 1)
        got = selection.select_candidates(
            self.conn, classes=classes, verdicts={"noreply@bank.com": "never"})
        self.assertEqual(got, [])

    def test_always_verdict_rescues_a_marketing_sender(self):
        """A human using a campaign tool is misclassed by address shape. The
        operator's decision has to win, or the correction is unusable."""
        self.msg("m1", sender="sam@mailchimpapp.com", hints=("pdf",))
        classes = {"sam@mailchimpapp.com": "marketing"}
        self.assertEqual(selection.select_candidates(self.conn, classes=classes), [])
        got = selection.select_candidates(
            self.conn, classes=classes, verdicts={"sam@mailchimpapp.com": "always"})
        self.assertEqual(len(got), 1)

    def test_domain_rule_covers_every_address_at_that_domain(self):
        self.msg("m1", sender="a@junk.com", hints=("pdf",))
        self.msg("m2", sender="b@junk.com", hints=("pdf",))
        got = selection.select_candidates(self.conn, verdicts={"@junk.com": "never"})
        self.assertEqual(got, [])

    def test_address_rule_beats_a_domain_rule(self):
        """A blanket '@bank.com never' must not silence the one human there."""
        self.msg("m1", sender="noreply@bank.com", hints=("pdf",))
        self.msg("m2", sender="mymanager@bank.com", hints=("pdf",))
        got = selection.select_candidates(
            self.conn, verdicts={"@bank.com": "never", "mymanager@bank.com": "always"})
        self.assertEqual([c["message_id"] for c in got], ["m2"])

    def test_verdicts_round_trip_through_sender_rules(self):
        selection.store_verdicts(self.conn, {"a@x.com": "never", "@y.com": "always"},
                                 reasons={"a@x.com": "300 msgs, 0 docs"})
        self.assertEqual(selection.load_rules(self.conn),
                         {"a@x.com": "never", "@y.com": "always"})

    def test_stored_verdict_is_updated_not_duplicated(self):
        selection.store_verdicts(self.conn, {"a@x.com": "never"})
        selection.store_verdicts(self.conn, {"a@x.com": "always"})
        self.assertEqual(selection.load_rules(self.conn), {"a@x.com": "always"})

    def test_review_rows_in_the_csv_are_not_decisions(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "v.csv"
            p.write_text("sender,verdict\na@x.com,review\nb@x.com,never\nc@x.com,\n")
            self.assertEqual(selection.load_verdicts(p), {"b@x.com": "never"})


class RankTest(Base):
    def test_important_human_mail_is_fetched_first(self):
        """Order is the only protection a truncated run has."""
        self.msg("bulk", sender="billing@utility.com", ts=9999, hints=("pdf",))
        self.msg("good", sender="sam@school.org", ts=1, hints=("pdf", "large"),
                 labels=("IMPORTANT", "CATEGORY_PERSONAL"))
        got = selection.select_candidates(
            self.conn, classes={"sam@school.org": "human",
                                "billing@utility.com": "service-transactional"})
        self.assertEqual([c["message_id"] for c in got], ["good", "bulk"])

    def test_ties_break_by_recency(self):
        self.msg("old", sender="a@x.com", ts=100, hints=("pdf",))
        self.msg("new", sender="a@x.com", ts=200, hints=("pdf",))
        got = selection.select_candidates(self.conn)
        self.assertEqual([c["message_id"] for c in got], ["new", "old"])


class CensusTest(Base):
    def test_census_counts_corpus_and_document_messages_separately(self):
        """A sender with many messages and no documents is the most actionable
        row in the report; the census has to be able to express it."""
        for i in range(5):
            self.msg(f"n{i}", sender="news@x.com")
        self.msg("d1", sender="news@x.com", hints=("pdf",))
        census = {r["sender"]: r for r in selection.sender_census(self.conn)}
        self.assertEqual(census["news@x.com"]["messages"], 6)
        self.assertEqual(census["news@x.com"]["doc_messages"], 1)

    def test_coverage_curve_counts_decisions_not_messages(self):
        for i in range(80):
            self.msg(f"a{i}", sender="loud@x.com")
        for i in range(20):
            self.msg(f"b{i}", sender="quiet@x.com")
        curve = selection.coverage_curve(selection.sender_census(self.conn))
        self.assertEqual(curve["50%"], 1)      # one decision covers 80%
        self.assertEqual(curve["95%"], 2)

    def test_suggestion_never_kills_a_sender_that_sent_a_document(self):
        rows = [{"sender_class": "marketing", "doc_messages": 3},
                {"sender_class": "marketing", "doc_messages": 0},
                {"sender_class": "human", "doc_messages": 0}]
        self.assertEqual([selection.suggest_verdict(r) for r in rows],
                         ["review", "never", "always"])


if __name__ == "__main__":
    unittest.main()


class EntryPointTest(unittest.TestCase):
    """Every CLI a handoff tells a human to type must at least import.

    Found the hard way: `mailctx.targetrun` did `from mailctx.sync import
    DB_PATH` while DB_PATH lived in `syncrun`. The module raised ImportError on
    the first line an operator would have run, inside a sudo session, against
    production. No test imported a CLI module, so nothing caught it.
    """

    MODULES = ("mailctx.sync", "mailctx.syncrun", "mailctx.targetrun",
               "mailctx.selectrun", "mailctx.mcp", "mailctx.preflight",
               "mailctx.authorize", "mailctx.targeting", "mailctx.selection")

    def test_every_module_imports(self):
        import importlib
        for name in self.MODULES:
            with self.subTest(module=name):
                importlib.import_module(name)

    def test_the_index_path_is_importable_from_one_place(self):
        from mailctx.sync import DB_PATH as a
        from mailctx.syncrun import DB_PATH as b
        self.assertEqual(a, b)

    def test_selectrun_parses_its_arguments(self):
        """The gate command must reach argparse, not an ImportError."""
        import contextlib
        import io
        import mailctx.selectrun as sr
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
            sr.main(["--help"])
        self.assertIn("--report-only", buf.getvalue())

import tempfile
import unittest
from pathlib import Path

import grocery
import insights
import render


class InsightsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = grocery.connect(Path(self.temp.name) / "t.sqlite3")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def trip(self, when, items, store="Costco"):
        """One completed shopping trip, backdated."""
        grocery.ingest_items(
            self.conn, store,
            grocery.parse_items_json(str(items).replace("'", '"')),
            "text", observed_at=when, actor="owner",
        )
        grocery.set_status(self.conn, store, items, "purchased", actor="owner")
        grocery.close_trip(self.conn, store, when)

    def weekly(self, weeks=6):
        from datetime import datetime, timedelta
        base = datetime.fromisoformat("2026-08-30T10:00:00-04:00")
        for w in range(weeks):
            when = (base - timedelta(weeks=weeks - 1 - w)).isoformat()
            self.trip(when, ["Milk", "Bananas"])

    def test_frequency_counts_only_confirmed_purchases(self):
        """`purchased` can be undone before a trip closes; only closing counts."""
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Milk"]'), "text"
        )
        grocery.set_status(self.conn, "Costco", ["Milk"], "purchased")
        grocery.set_status(self.conn, "Costco", ["Milk"], "needed")
        self.assertEqual(insights.purchase_history(self.conn, "Costco"), [])

    def test_interval_is_the_mean_gap(self):
        self.weekly(6)
        milk = next(r for r in insights.purchase_history(self.conn, "Costco")
                    if r["name"] == "Milk")
        self.assertEqual(milk["times"], 6)
        self.assertEqual(insights.typical_interval_days(milk), 7.0)

    def test_a_single_purchase_has_no_rhythm(self):
        self.trip("2026-08-01T10:00:00-04:00", ["Saffron"])
        row = next(r for r in insights.purchase_history(self.conn, "Costco")
                   if r["name"] == "Saffron")
        self.assertIsNone(insights.typical_interval_days(row))
        # ...and is never suggested, however long ago it was.
        overdue = insights.due(self.conn, "Costco", now="2027-01-01T10:00:00-04:00")
        self.assertNotIn("Saffron", [r["name"] for r in overdue])

    def test_due_finds_what_is_late_and_absent(self):
        self.weekly(6)
        overdue = insights.due(self.conn, "Costco", now="2026-09-09T10:00:00-04:00")
        self.assertEqual(sorted(r["name"] for r in overdue), ["Bananas", "Milk"])
        self.assertAlmostEqual(overdue[0]["overdue_by_days"], 3.0, places=1)

    def test_nothing_is_due_right_after_a_trip(self):
        self.weekly(6)
        self.assertEqual(
            insights.due(self.conn, "Costco", now="2026-08-31T08:00:00-04:00"), []
        )

    def test_something_already_on_the_list_is_not_suggested(self):
        self.weekly(6)
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Milk"]'), "text"
        )
        overdue = insights.due(self.conn, "Costco", now="2026-09-09T10:00:00-04:00")
        self.assertNotIn("Milk", [r["name"] for r in overdue])

    def test_section_filter_answers_the_produce_question(self):
        self.weekly(6)
        produce = insights.due(
            self.conn, "Costco", section="produce", now="2026-09-09T10:00:00-04:00"
        )
        self.assertEqual([r["name"] for r in produce], ["Bananas"])
        dairy = insights.due(
            self.conn, "Costco", section="dairy", now="2026-09-09T10:00:00-04:00"
        )
        self.assertEqual([r["name"] for r in dairy], ["Milk"])

    def test_slack_suppresses_nagging(self):
        self.weekly(6)
        just_over = "2026-09-06T12:00:00-04:00"   # 7.1 days, barely due
        self.assertTrue(insights.due(self.conn, "Costco", now=just_over))
        self.assertEqual(
            insights.due(self.conn, "Costco", now=just_over, slack=1.5), []
        )

    def test_rendered_answer_is_bilingual(self):
        self.weekly(6)
        rows = insights.due(self.conn, "Costco", "produce",
                            now="2026-09-09T10:00:00-04:00")
        en = render.render_due("Costco", rows, "en", "produce")
        pt = render.render_due("Costco", rows, "pt", "produce")
        self.assertIn("in Produce", en)
        self.assertIn("em Hortifrúti", pt)
        self.assertIn("Bananas", pt)
        self.assertEqual(
            render.render_due("Costco", [], "pt", "produce"),
            "Nada parece atrasado em Costco em Hortifrúti.",
        )


if __name__ == "__main__":
    unittest.main()

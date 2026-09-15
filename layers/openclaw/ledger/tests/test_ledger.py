"""Tests for the ledger cascade, templates and schema. Stdlib unittest.

Every fixture below is a real subject/snippet pair copied out of
/tmp/mc-snap.sqlite, not an invented one. Where a test asserts `amount is
None` that is deliberate: the Gmail snippet is 200 characters and the amount
falls past the cut. Recording that honestly is the point.

    python3 -m unittest discover -s layers/openclaw/ledger/tests
"""
from __future__ import annotations

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

import templates as T                                             # noqa: E402
from cascade import (Cascade, Message, Row, clean, collapse, generic,   # noqa: E402
                     link_events, link_key, parse_amount, parse_date, ts_date)

TPL = T.load()


def run_one(sender: str, subject: str, snippet: str, ts: int = 1750000000):
    """Push one message through L1 for `sender`."""
    tpl = TPL[sender]
    return tpl.apply(Message("m1", sender, subject, snippet, ts), "merchant:x", "X")


class TestNormalising(unittest.TestCase):
    def test_strips_bulk_sender_padding(self):
        # Amazon pads subjects with U+034F/U+200C to defeat clipping.
        raw = 'Shipped: "Lavazza Super Crema Whole..."͏ ‌ ͏ ‌'
        self.assertEqual(clean(raw), 'Shipped: "Lavazza Super Crema Whole..."')

    def test_unescapes_entities(self):
        self.assertEqual(clean("Habit Burger &amp; Grill"), "Habit Burger & Grill")


class TestAmounts(unittest.TestCase):
    def test_us(self):
        self.assertEqual(parse_amount("$202.16"), (202.16, "USD"))

    def test_us_thousands(self):
        self.assertEqual(parse_amount("$1,300.00"), (1300.0, "USD"))

    def test_brl(self):
        self.assertEqual(parse_amount("R$ 1.234,56"), (1234.56, "BRL"))

    def test_venmo_display_spacing(self):
        # "You paid Sam Example $ 160 . 00"
        self.assertEqual(parse_amount("$ 160 . 00"), (160.0, "USD"))

    def test_no_amount(self):
        self.assertEqual(parse_amount(""), (None, None))


class TestDates(unittest.TestCase):
    def test_us_slash(self):
        self.assertEqual(parse_date("06/30/2026"), "2026-06-30")

    def test_uk_slash_disambiguated_by_value(self):
        # A UK retailer writes "Date ordered: 17/07/2025".
        self.assertEqual(parse_date("17/07/2025"), "2025-07-17")

    def test_uk_slash_needs_dayfirst_when_ambiguous(self):
        self.assertEqual(parse_date("11/12/2024", dayfirst=True), "2024-12-11")
        self.assertEqual(parse_date("11/12/2024"), "2024-11-12")

    def test_named_month(self):
        self.assertEqual(parse_date("Jun 6, 2026"), "2026-06-06")
        self.assertEqual(parse_date("February 8, 2026"), "2026-02-08")

    def test_year_from_message_when_omitted(self):
        # A booking site: "Appointment with Dr. Example on 5/22"
        ts = int(__import__("datetime").datetime(2026, 5, 20).timestamp())
        self.assertEqual(parse_date("5/22", ts), "2026-05-22")

    def test_invalid_returns_none(self):
        self.assertIsNone(parse_date("no date here"))
        self.assertIsNone(parse_date("13/45/2025"))


class TestTemplatesOnRealSnippets(unittest.TestCase):
    def test_quickbooks_payment_names_acme_not_intuit(self):
        o = run_one("quickbooks@notification.intuit.com",
                    "Payment confirmation: Invoice #59115-(Acme Lawn for all your Landscaping needs.)",
                    "Intuit QuickBooks Manage payment You paid $202.16 to Acme Lawn for all your "
                    "Landscaping needs. on 06/30/2026 Payment details Invoice no. 59115 "
                    "Invoice amount $202.16 Total amount $202.16 Status Paid")
        self.assertEqual(o.action, "extract")
        r = o.rows[0]
        self.assertEqual((r.kind, r.amount, r.currency), ("payment", 202.16, "USD"))
        self.assertEqual(r.ref_number, "59115")
        self.assertEqual(r.date, "2026-06-30")
        self.assertIn("Acme Lawn", r.counterparty)

    def test_venmo_subject_beats_contradictory_snippet(self):
        # Real message: subject says Caio paid, the preheader says the reverse.
        o = run_one("venmo@venmo.com", "You paid Sam Example $140.00",
                    "Sam Example paid you $140.00 You paid Sam Example $ 140 . 00 "
                    "Faxina See transaction Transaction details Date Dec 04, 2024 "
                    "Transaction ID 4216014379232434457 Payment Method EXAMPLE BANK USA, NA")
        r = o.rows[0]
        self.assertEqual((r.counterparty, r.amount, r.kind),
                         ("Sam Example", 140.0, "payment"))
        self.assertEqual(r.ref_number, "4216014379232434457")
        self.assertEqual(r.date, "2024-12-04")

    def test_venmo_request_is_terminated_not_extracted(self):
        o = run_one("venmo@venmo.com", "Sam Example requests $140.00",
                    "Sam Example requests $ 140 . 00 See Request")
        self.assertEqual(o.action, "terminate")

    def test_paypal_ref_has_no_label_leak(self):
        o = run_one("service@paypal.com", "Google: $22.99 USD",
                    "Payment details are inside. Hello, Alex Example You paid $22.99 USD to "
                    "Google Merchant Google noreply+support@goog... Transaction date "
                    "Feb 23, 2026 Order ID 616106550346531283 View Payment Details")
        r = o.rows[0]
        self.assertEqual(r.counterparty, "Google")
        self.assertEqual(r.ref_number, "616106550346531283")   # not "Order ID 6161..."
        self.assertEqual(r.amount, 22.99)

    def test_njtransit_both_layouts(self):
        a = run_one("noreply@mytix.njtransit.com", "NJ TRANSIT MyTix - Receipt",
                    "NJ TRANSIT Your Ticket Purchase on 10/22/2025 08:24:51 Trx Seq Id : "
                    "281373217 Ticket No.(s): 390630911,390630912 Ticket Details")
        b = run_one("noreply@mytix.njtransit.com", "NJ TRANSIT - Receipt",
                    "Hi, Receipt for Purchase on 06/07/2026 02:12:58 Please find the details "
                    "of your purchase below. Transaction Seq ID: 2103258140 Ticket No.(s): "
                    "2105263144,2105263145 Ticket Details")
        self.assertEqual((a.rows[0].ref_number, a.rows[0].date), ("281373217", "2025-10-22"))
        self.assertEqual((b.rows[0].ref_number, b.rows[0].date), ("2103258140", "2026-06-07"))
        # The Amount column falls past the 200-char snippet cut in both layouts.
        self.assertIsNone(a.rows[0].amount)

    def test_apple_subscription_vs_bare_receipt(self):
        sub = run_one("no_reply@email.apple.com", "Your receipt from Apple.",
                      "Receipt February 8, 2026 Order ID: MMQGL5N3B0 Document: 810088895385 "
                      "Apple Account: owner@example.com Apple News+ Monthly Renews "
                      "March 8, 2026 Alex's iPhone (2) $12.99 Billing and Payment Alex")
        self.assertEqual(sub.rows[0].kind, "subscription")
        self.assertEqual(sub.rows[0].amount, 12.99)
        self.assertEqual(sub.rows[0].date, "2026-02-08")
        bare = run_one("no_reply@email.apple.com", "Your receipt from Apple.",
                       "Receipt APPLE ACCOUNT owner@example.com BILLED TO Apple Card Alex "
                       "Example 100 Example St Apt 1 New York, NY 10001 USA DATE Nov 11, 2024 "
                       "ORDER ID MMQFBXMN19 DOCUMENT NO. 195874113642 App Store Starfall")
        # No "Renews" line survives the cut, so it must NOT be called a
        # subscription on faith -- that judgement belongs to the model lane.
        self.assertEqual(bare.rows[0].kind, "purchase")
        self.assertIsNone(bare.rows[0].amount)

    def test_grubhub_counterparty_is_the_restaurant(self):
        o = run_one("orders@eat.grubhub.com", "Thanks for your Example Bowls order",
                    "ETA 4:02 PM - 4:17 PM to 1 Example Ave, Anytown, NJ GRUBHUB")
        self.assertEqual(o.rows[0].counterparty, "Example Bowls")

    def test_terminate_rules_actually_drop(self):
        for sender, subject, snippet in [
            ("order-update@amazon.com", "Alex Example, will you rate your transaction at Amazon.com?", ""),
            ("no_reply@email.apple.com", "Your Subscription is Expiring", "Subscription Expiring"),
            ("noreply@stubhub.com", "Your feedback is important to us", "We'd love your feedback"),
            ("no.reply.alerts@chase.com", "Your credit card statement is available",
             "Statement ready Minimum payment due $115.00"),
        ]:
            with self.subTest(sender=sender):
                self.assertEqual(run_one(sender, subject, snippet).action, "terminate")

    def test_every_template_compiles_and_is_json_round_trippable(self):
        for sender, t in TPL.items():
            with self.subTest(sender=sender):
                self.assertTrue(t.rules, f"{sender} has no rules")
                json.loads(json.dumps(t.raw_rules))
                for r in t.raw_rules:
                    self.assertIn(r["action"], ("extract", "terminate"))
                    if r["action"] == "extract":
                        self.assertIn(r["kind"],
                                      {"purchase", "subscription", "booking",
                                       "appointment", "payment", "shipment"})


class TestGenericLane(unittest.TestCase):
    def test_extracts_when_amount_present(self):
        o = generic(Message("m", "x@y.com", "Online Order Receipt for $75.53 at Example Bistro",
                            "Example Bistro - (555) 010-0199 Check #9 for ALEX EXAMPLE", 1750000000),
                    "merchant:y", "Example Bistro")
        self.assertEqual(o.action, "extract")
        self.assertEqual(o.rows[0].amount, 75.53)

    def test_escalates_rather_than_guessing(self):
        o = generic(Message("m", "x@y.com", "Your order is confirmed", "Thanks!", 1750000000),
                    "merchant:y", "Y")
        self.assertEqual(o.action, "escalate")

    def test_terminates_review_solicitation(self):
        o = generic(Message("m", "x@y.com", "How was your order #EXD23050?",
                            "Your artwork should have been delivered recently.", 1750000000),
                    "merchant:y", "Example Designs")
        self.assertEqual(o.action, "terminate")

    def test_no_cue_terminates(self):
        o = generic(Message("m", "x@y.com", "The 57 best gifts under $100",
                            "Plus: my favourite gift", 1750000000), "merchant:y", "NYT")
        self.assertEqual(o.action, "terminate")


class TestCascadeOrder(unittest.TestCase):
    def test_marketing_terminates_before_any_work(self):
        c = Cascade(TPL, {"promo@shop.com": "marketing"},
                    lambda a: ("merchant:shop.com", "Shop"))
        o = c.run(Message("m", "promo@shop.com", "You paid $10.00", "receipt", 1750000000),
                  "promo@shop.com")
        self.assertEqual((o.action, o.layer), ("terminate", "L0"))

    def test_template_wins_over_generic(self):
        c = Cascade(TPL, {"orders@eat.grubhub.com": "service-transactional"},
                    lambda a: ("merchant:grubhub.com", "Grubhub"))
        o = c.run(Message("m", "orders@eat.grubhub.com", "Thanks for your Example Bistro order",
                          "ETA 1:00 PM", 1750000000), "orders@eat.grubhub.com")
        self.assertEqual(o.layer, "L1")
        self.assertEqual(o.rows[0].counterparty, "Example Bistro")


def _row(**kw):
    base = dict(entity_key="merchant:a", entity_name="A", counterparty="A",
                date="2025-01-01", kind="purchase", amount=None, currency=None,
                ref_number=None, service_dates=None, message_id="m",
                confidence=0.7, lane="L1", rule="r")
    base.update(kw)
    return Row(**base)


class TestEventLinking(unittest.TestCase):
    def test_ref_links_three_messages_into_one_event(self):
        rows = [_row(message_id="m1", kind="purchase", ref_number="114-1234567-1234567", amount=42.0),
                _row(message_id="m2", kind="shipment", ref_number="114-1234567-1234567"),
                _row(message_id="m3", kind="shipment", ref_number="114-1234567-1234567")]
        link_events(rows)
        self.assertEqual(len({r.event_id for r in rows}), 1)
        kept = collapse(rows)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].amount, 42.0)   # the row with the money wins

    def test_title_links_amazon_when_the_order_number_is_truncated(self):
        rows = [_row(message_id="m1", description='"Lavazza Super Crema Whole..."'),
                _row(message_id="m2", kind="shipment",
                     description='Shipped: "Lavazza Super Crema Whole..."')]
        link_events(rows)
        self.assertEqual(rows[0].link_basis, "title")
        self.assertEqual(rows[0].event_id, rows[1].event_id)

    def test_distinctive_ref_links_across_entities(self):
        # The delivery notice came from exampledesigns.com, the review request
        # from reviews.example; one order, two senders.
        rows = [_row(entity_key="merchant:exampledesigns.com", ref_number="EXD23050"),
                _row(entity_key="merchant:reviews.example", ref_number="EXD23050", message_id="m2")]
        link_events(rows)
        self.assertEqual(rows[0].event_id, rows[1].event_id)

    def test_short_numeric_ref_stays_entity_scoped(self):
        rows = [_row(entity_key="merchant:a", ref_number="59115"),
                _row(entity_key="merchant:b", ref_number="59115", message_id="m2")]
        link_events(rows)
        self.assertNotEqual(rows[0].event_id, rows[1].event_id)

    def test_booking_links_on_counterparty_and_iso_date(self):
        rows = [_row(kind="booking", counterparty="SILVIA", date="2026-07-21",
                     service_dates="Tue, Jul. 21 at 7:30pm", entity_key="merchant:resy.com"),
                _row(kind="booking", counterparty="SILVIA", date="2026-07-21", message_id="m2",
                     service_dates="Tue Jul 21, 2026 7:30pm", entity_key="merchant:google.com")]
        link_events(rows)
        self.assertEqual(rows[0].event_id, rows[1].event_id)

    def test_unlinkable_rows_stay_separate(self):
        rows = [_row(message_id="m1"), _row(message_id="m2")]
        link_events(rows)
        self.assertEqual({r.link_basis for r in rows}, {"none"})
        self.assertEqual(len({r.event_id for r in rows}), 2)


class TestSchema(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        with open(os.path.join(ROOT, "schema.sql")) as fh:
            self.db.executescript(fh.read())
        self.db.execute("INSERT INTO extraction_runs (run_id,started_at,source,lane,extractor)"
                        " VALUES (1,'2026-08-25','snap','deterministic','v1')")
        self.db.execute("INSERT INTO entities (entity_id,entity_key,name,kind)"
                        " VALUES (1,'merchant:x','X','merchant')")

    def _txn(self, **kw):
        d = dict(entity_id=1, counterparty="X", date="2026-01-01", kind="payment",
                 amount=1.0, currency="USD", message_id="m1", confidence=0.9,
                 lane="L1", is_primary=1, run_id=1)
        d.update(kw)
        cols = ",".join(d)
        self.db.execute(f"INSERT INTO transactions ({cols}) VALUES "
                        f"({','.join('?' * len(d))})", tuple(d.values()))

    def test_kind_is_constrained(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._txn(kind="refund")

    def test_currency_is_constrained(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._txn(currency="EUR")
        self._txn(currency=None, message_id="m2")     # unknown currency is allowed

    def test_entity_key_is_unique(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO entities (entity_key,name,kind)"
                            " VALUES ('merchant:x','Dup','merchant')")

    def test_same_message_cannot_yield_the_same_kind_twice_in_a_run(self):
        self._txn()
        with self.assertRaises(sqlite3.IntegrityError):
            self._txn()

    def test_a_second_run_supersedes_rather_than_collides(self):
        self._txn()
        self.db.execute("INSERT INTO extraction_runs (run_id,started_at,source,lane,extractor)"
                        " VALUES (2,'2026-09-01','snap','model','v2')")
        self._txn(run_id=2, amount=2.0)
        self.assertEqual(self.db.execute("select count(*) from transactions").fetchone()[0], 2)

    def test_ledger_view_shows_only_primary_rows(self):
        self._txn(is_primary=1, message_id="m1")
        self._txn(is_primary=0, message_id="m2", kind="shipment")
        self.assertEqual(self.db.execute("select count(*) from ledger").fetchone()[0], 1)
        self.assertEqual(
            self.db.execute("select count(*) from ledger_evidence").fetchone()[0], 2)

    def test_deleting_an_entity_takes_its_transactions(self):
        self.db.execute("PRAGMA foreign_keys=ON")
        self._txn()
        self.db.execute("DELETE FROM entities WHERE entity_id=1")
        self.assertEqual(self.db.execute("select count(*) from transactions").fetchone()[0], 0)


class TestBuiltLedger(unittest.TestCase):
    """Runs only if the prototype output is present."""
    # Points at a locally built ledger; unset means the class skips.
    DB = os.environ.get("LEDGER_PROTOTYPE_DB", "")

    def setUp(self):
        if not os.path.exists(self.DB):
            self.skipTest("prototype ledger not built")
        self.db = sqlite3.connect(f"file:{self.DB}?mode=ro", uri=True)

    def test_acme_is_queryable_by_name(self):
        n, total = self.db.execute(
            "select count(*), sum(amount) from ledger "
            "where counterparty like '%cme Lawn%' and amount is not null").fetchone()
        self.assertGreaterEqual(n, 15)
        self.assertGreater(total, 1000)

    def test_every_transaction_points_at_a_real_message(self):
        bad = self.db.execute(
            "select count(*) from transactions where message_id is null or message_id=''"
        ).fetchone()[0]
        self.assertEqual(bad, 0)

    def test_templates_are_persisted_with_their_rules(self):
        n, = self.db.execute("select count(*) from extraction_templates").fetchone()
        self.assertGreater(n, 40)
        for sender, rules in self.db.execute(
                "select sender, rules_json from extraction_templates"):
            self.assertTrue(json.loads(rules), sender)

    def test_no_orphan_transactions(self):
        n, = self.db.execute(
            "select count(*) from transactions t left join entities e using (entity_id)"
            " where e.entity_id is null").fetchone()
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

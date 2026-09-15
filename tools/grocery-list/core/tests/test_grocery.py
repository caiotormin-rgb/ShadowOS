import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import db
import grocery
import people
import render


class ParserTests(unittest.TestCase):
    """The parser is built at import-of-command time, so a duplicate flag or a
    bad choices list only explodes at runtime. Build it here instead."""

    def test_parser_builds(self):
        self.assertIsNotNone(grocery.build_parser())

    def test_every_command_parses_a_minimal_invocation(self):
        parser = grocery.build_parser()
        invocations = [
            ["init"], ["stores"], ["events"],
            ["add", "--store", "X", "milk"],
            ["ingest", "--store", "X", "--source-type", "text",
             "--items-json", '["milk"]'],
            ["list"], ["list", "--format", "text", "--lang", "pt"],
            ["buy", "milk"], ["unbuy", "milk"], ["remove", "milk"],
            ["close"], ["reopen"], ["history"],
            ["section", "show", "milk"], ["section", "set", "milk", "dairy"],
            ["section", "list"], ["section", "unknown"],
            ["layout"], ["layout", "--set", "warehouse"],
            ["allow", "list"], ["share", "--contact", "Mom"], ["delivered", "1"],
        ]
        for argv in invocations:
            with self.subTest(argv=argv):
                self.assertIsNotNone(parser.parse_args(argv))

    def test_read_commands_accept_an_actor(self):
        """The plugin identifies the caller on reads too; without --actor on
        these, enrolling the first member takes `list` down."""
        parser = grocery.build_parser()
        for argv in (["list"], ["history"], ["stores"], ["events"], ["due"]):
            with self.subTest(argv=argv):
                args = parser.parse_args(argv + ["--actor", "+15551234567"])
                self.assertEqual(args.actor, "+15551234567")

    def test_mutating_commands_accept_format_and_lang(self):
        parser = grocery.build_parser()
        for argv in (["add", "--store", "X", "milk"], ["buy", "milk"],
                     ["remove", "milk"], ["close"], ["reopen"]):
            with self.subTest(argv=argv):
                args = parser.parse_args(argv + ["--format", "text", "--lang", "pt"])
                self.assertEqual((args.format, args.lang), ("text", "pt"))


class GroceryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "test.sqlite3"
        self.conn = grocery.connect(self.db)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_multisource_dedup_and_quantity(self):
        grocery.ingest_items(
            self.conn,
            "Costco",
            grocery.parse_items_json('[{"name":" milk ","quantity":1}]'),
            "voice",
            "voice-1.ogg",
        )
        result = grocery.ingest_items(
            self.conn,
            "costco",
            grocery.parse_items_json('[{"name":"Milk","quantity":2,"note":"2%"}]'),
            "image",
            "fridge.jpg",
        )
        self.assertEqual(result["merged"], ["Milk"])
        _, rows = grocery.current_items(self.conn, "Costco")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quantity"], 2)
        self.assertEqual(rows[0]["note"], "2%")
        source_count = self.conn.execute("SELECT COUNT(*) FROM item_sources").fetchone()[0]
        self.assertEqual(source_count, 2)

    def test_sourdough_bread_dedupes_across_portuguese_and_english(self):
        grocery.ingest_items(
            self.conn,
            "Costco",
            grocery.parse_items_json('[{"name":"sourdough bread"}]'),
            "text",
        )
        result = grocery.ingest_items(
            self.conn,
            "Costco",
            grocery.parse_items_json('[{"name":"pao sourdough"}]'),
            "text",
        )

        self.assertEqual(
            (result["added"], result["merged"]), ([], ["Pao sourdough"])
        )
        _, rows = grocery.current_items(self.conn, "Costco")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Sourdough bread")
        self.assertEqual(rows[0]["canonical_name"], "sourdough bread")

    def test_product_url_is_normalized_stored_and_updated_on_merge(self):
        first = grocery.parse_items_json(json.dumps([{
            "name": "Coffee",
            "productUrl": "HTTPS://Example.COM:443/product/coffee?size=2#reviews",
        }]))
        grocery.ingest_items(self.conn, "Amazon", first, "url")
        row = self.conn.execute("SELECT * FROM items").fetchone()
        self.assertEqual(
            row["product_url"], "https://example.com/product/coffee?size=2"
        )

        second = grocery.parse_items_json(json.dumps([{
            "name": "Coffee",
            "productUrl": "https://www.amazon.com/dp/B012345678",
        }]))
        grocery.ingest_items(self.conn, "Amazon", second, "url")
        row = self.conn.execute("SELECT * FROM items").fetchone()
        self.assertEqual(row["product_url"], "https://www.amazon.com/dp/B012345678")
        self.assertEqual(
            self.conn.execute("SELECT product_url FROM events ORDER BY id DESC").fetchone()[0],
            row["product_url"],
        )

    def test_product_url_rejects_non_http_credentials_and_controls(self):
        invalid = [
            "file:///etc/passwd",
            "https://user:secret@example.com/item",
            "https://example.com/a b",
            "not a URL",
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(grocery.GroceryError):
                grocery.parse_items_json(json.dumps([{"name": "Coffee", "productUrl": value}]))

    def test_product_url_survives_close_and_reopen(self):
        items = grocery.parse_items_json(json.dumps([{
            "name": "Coffee", "productUrl": "https://example.com/coffee"
        }]))
        grocery.ingest_items(self.conn, "Amazon", items, "url")
        grocery.set_status(self.conn, "Amazon", ["Coffee"], "purchased")
        grocery.close_trip(self.conn, "Amazon")
        self.assertEqual(
            self.conn.execute("SELECT product_url FROM trip_items").fetchone()[0],
            "https://example.com/coffee",
        )
        grocery.reopen_trip(self.conn, "Amazon")
        self.assertEqual(
            self.conn.execute("SELECT product_url FROM items").fetchone()[0],
            "https://example.com/coffee",
        )

    def test_close_archives_purchased_and_rolls_missing(self):
        items = grocery.parse_items_json('["Milk", "Eggs"]')
        grocery.ingest_items(self.conn, "ShopRite", items, "text")
        grocery.set_status(self.conn, "ShopRite", ["Milk"], "purchased")
        result = grocery.close_trip(self.conn, "ShopRite", "2026-08-25T18:00:00-04:00")
        self.assertEqual(result["purchased"], ["Milk"])
        self.assertEqual(result["carried_forward"], ["Eggs"])
        _, rows = grocery.current_items(self.conn, "ShopRite")
        self.assertEqual([row["name"] for row in rows], ["Eggs"])
        outcomes = dict(self.conn.execute("SELECT name, outcome FROM trip_items").fetchall())
        self.assertEqual(outcomes, {"Milk": "purchased", "Eggs": "missing"})

    def test_ambiguity_assumed_when_reversible_refused_when_not(self):
        grocery.ingest_items(
            self.conn,
            "Costco",
            grocery.parse_items_json(
                '[{"name":"Milk","quantity":1,"unit":"gal"},'
                ' {"name":"Milk","quantity":2,"unit":"L"}]'
            ),
            "text",
        )
        _, rows = grocery.current_items(self.conn, "Costco")
        self.assertEqual(len(rows), 2)

        # Reversible action: assume rather than ask, but say what was assumed.
        result = grocery.set_status(self.conn, "Costco", ["Milk"], "purchased")
        self.assertEqual(result["items"], ["Milk"])
        self.assertTrue(result["assumptions"])
        assumption = result["assumptions"][0]
        self.assertEqual(assumption["kind"], "item_ambiguous")
        self.assertEqual(assumption["matches"], 2)
        self.assertIn("gal", assumption["choices"])
        self.assertEqual(assumption["chosen"], "gal")
        statuses = dict(self.conn.execute("SELECT unit, status FROM items").fetchall())
        self.assertEqual(statuses, {"gal": "purchased", "l": "needed"})

        # Destructive action on the same ambiguity still refuses.
        with self.assertRaises(grocery.GroceryError) as caught:
            grocery.remove_items(self.conn, "Costco", ["Milk"])
        self.assertIn("--unit", str(caught.exception))

        statuses = dict(
            self.conn.execute("SELECT unit, status FROM items").fetchall()
        )
        self.assertEqual(statuses, {"gal": "purchased", "l": "needed"})

    def test_remove_targets_only_the_named_unit(self):
        grocery.ingest_items(
            self.conn,
            "Costco",
            grocery.parse_items_json(
                '[{"name":"Milk","unit":"gal"}, {"name":"Milk","unit":"L"}]'
            ),
            "text",
        )
        with self.assertRaises(grocery.GroceryError):
            grocery.remove_items(self.conn, "Costco", ["Milk"])

        grocery.remove_items(self.conn, "Costco", ["Milk"], grocery.unit_filter("L"))
        _, rows = grocery.current_items(self.conn, "Costco")
        self.assertEqual([row["unit"] for row in rows], ["gal"])

    def test_unit_filter_selects_the_unitless_row(self):
        grocery.ingest_items(
            self.conn,
            "Costco",
            grocery.parse_items_json('[{"name":"Eggs"}, {"name":"Eggs","unit":"dozen"}]'),
            "text",
        )
        grocery.remove_items(self.conn, "Costco", ["Eggs"], grocery.unit_filter(""))
        _, rows = grocery.current_items(self.conn, "Costco")
        self.assertEqual([row["unit"] for row in rows], ["dozen"])

    def test_unique_name_still_resolves_without_a_unit(self):
        grocery.ingest_items(
            self.conn,
            "Costco",
            grocery.parse_items_json('[{"name":"Bananas","unit":"bunch"}]'),
            "text",
        )
        self.assertEqual(
            grocery.set_status(self.conn, "Costco", ["bananas"], "purchased")["items"],
            ["Bananas"],
        )

    def test_history_survives_close_and_remove(self):
        """The failure 6a exists to fix: item_sources cascaded history away."""
        grocery.ingest_items(
            self.conn,
            "Costco",
            grocery.parse_items_json('["Milk", "Bananas"]'),
            "voice",
            "note.ogg",
            actor="owner",
        )
        grocery.set_status(self.conn, "Costco", ["Milk"], "purchased", actor="owner")
        grocery.close_trip(self.conn, "Costco", "2026-08-30T18:00:00-04:00")
        grocery.remove_items(self.conn, "Costco", ["Bananas"])

        # Nothing is left in items or item_sources; the log still has it all.
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0], 0
        )
        actions = [
            (r["item_name"], r["action"])
            for r in self.conn.execute(
                "SELECT item_name, action FROM events ORDER BY id"
            )
        ]
        self.assertEqual(
            actions,
            [
                ("Milk", "added"),
                ("Bananas", "added"),
                ("Milk", "purchased"),
                # close_trip walks current_items, which sorts needed first.
                ("Bananas", "trip_missing"),
                ("Milk", "trip_purchased"),
                ("Bananas", "removed"),
            ],
        )

    def test_events_capture_actor_source_and_time(self):
        grocery.ingest_items(
            self.conn,
            "Costco",
            grocery.parse_items_json('[{"name":"Milk","quantity":2,"unit":"gal"}]'),
            "image",
            "fridge.jpg",
            observed_at="2026-08-30T09:15:00-04:00",
            actor="+15551234567",
        )
        row = self.conn.execute("SELECT * FROM events").fetchone()
        self.assertEqual(row["actor"], "+15551234567")
        self.assertEqual(row["source_type"], "image")
        self.assertEqual(row["source_ref"], "fridge.jpg")
        self.assertEqual(row["occurred_at"], "2026-08-30T09:15:00-04:00")
        self.assertEqual(row["store"], "Costco")
        self.assertEqual(row["unit"], "gal")
        self.assertEqual(row["quantity"], 2)

    def test_actor_is_optional_until_idea_two(self):
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Eggs"]'), "text"
        )
        self.assertIsNone(
            self.conn.execute("SELECT actor FROM events").fetchone()["actor"]
        )

    def test_events_have_no_cascading_foreign_keys(self):
        """A store deletion must not take the history with it."""
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Milk"]'), "text"
        )
        self.conn.execute("DELETE FROM stores")
        self.conn.commit()
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1
        )

    def test_store_falls_back_to_the_last_one_touched(self):
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Milk"]'), "text"
        )
        name, assumption = grocery.resolve_store(self.conn, None)
        self.assertEqual(name, "Costco")
        self.assertEqual(assumption, {"kind": "store_last_touched", "store": "Costco"})
        # Prose lives in the render layer, so a pt reply is not half English.
        self.assertEqual(
            render.render_assumption(assumption, "pt"),
            "usei Costco, a última loja mexida",
        )
        # An explicit store is never an assumption.
        self.assertEqual(grocery.resolve_store(self.conn, "ShopRite"), ("ShopRite", None))

    def test_store_resolution_fails_only_with_nothing_to_go_on(self):
        with self.assertRaises(grocery.GroceryError):
            grocery.resolve_store(self.conn, None)

    def test_buying_an_unlisted_item_records_it_as_bought(self):
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Milk"]'), "text"
        )
        result = grocery.set_status(self.conn, "Costco", ["Cilantro"], "purchased")
        self.assertEqual(result["items"], ["Cilantro"])
        self.assertEqual(
            result["assumptions"][0],
            {"kind": "item_not_listed", "name": "Cilantro"},
        )
        _, rows = grocery.current_items(self.conn, "Costco")
        self.assertEqual(
            {r["name"]: r["status"] for r in rows},
            {"Milk": "needed", "Cilantro": "purchased"},
        )
        actions = [
            r["action"] for r in self.conn.execute(
                "SELECT action FROM events WHERE normalized_name = 'cilantro' ORDER BY id"
            )
        ]
        self.assertEqual(actions, ["added", "purchased"])

    def test_unbuying_an_unlisted_item_still_errors(self):
        """Only buying invents a row; unbuy has no aisle to be standing in."""
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Milk"]'), "text"
        )
        with self.assertRaises(grocery.GroceryError):
            grocery.set_status(self.conn, "Costco", ["Cilantro"], "needed")

    def _stock(self, store="ShopRite"):
        grocery.ingest_items(
            self.conn, store, grocery.parse_items_json('["Milk", "Eggs"]'), "text"
        )
        grocery.set_status(self.conn, store, ["Milk"], "purchased")

    def test_duplicate_close_does_not_invent_a_second_trip(self):
        """The unstable-service failure: a retried close rolled items twice."""
        self._stock()
        first = grocery.close_trip(self.conn, "ShopRite")
        self.assertFalse(first["duplicate"])

        second = grocery.close_trip(self.conn, "ShopRite")
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["trip_id"], first["trip_id"])
        self.assertEqual(second["purchased"], first["purchased"])
        self.assertEqual(second["carried_forward"], first["carried_forward"])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0], 1
        )

    def test_a_real_second_trip_still_closes(self):
        """Activity after a close means the next close is genuine."""
        self._stock()
        grocery.close_trip(self.conn, "ShopRite")
        grocery.set_status(self.conn, "ShopRite", ["Eggs"], "purchased")
        second = grocery.close_trip(self.conn, "ShopRite")
        self.assertFalse(second["duplicate"])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0], 2
        )

    def test_close_outside_the_window_is_not_a_duplicate(self):
        self._stock()
        grocery.close_trip(self.conn, "ShopRite", "2026-08-30T10:00:00-04:00")
        later = grocery.close_trip(self.conn, "ShopRite", "2026-08-30T14:00:00-04:00")
        self.assertFalse(later["duplicate"])

    def test_reopen_restores_the_archived_trip(self):
        self._stock()
        trip = grocery.close_trip(self.conn, "ShopRite")
        _, after_close = grocery.current_items(self.conn, "ShopRite")
        self.assertEqual([r["name"] for r in after_close], ["Eggs"])

        result = grocery.reopen_trip(self.conn, "ShopRite")
        self.assertEqual(result["trip_id"], trip["trip_id"])
        self.assertEqual(sorted(result["restored"]), ["Eggs", "Milk"])

        _, rows = grocery.current_items(self.conn, "ShopRite")
        self.assertEqual(
            {r["name"]: r["status"] for r in rows},
            {"Milk": "purchased", "Eggs": "needed"},
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0], 0
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM events WHERE action = 'reopened'"
            ).fetchone()[0],
            2,
        )

    def test_reopen_needs_a_trip(self):
        with self.assertRaises(grocery.GroceryError):
            grocery.reopen_trip(self.conn, "ShopRite")

    def test_sections_resolve_and_are_learned_once(self):
        self.assertEqual(grocery.section_for(self.conn, "leite"), "dairy")
        # Curated hits are written through, so the map is consulted once.
        stored = self.conn.execute(
            "SELECT section, source FROM product_sections WHERE normalized_name = 'leite'"
        ).fetchone()
        self.assertEqual((stored["section"], stored["source"]), ("dairy", "curated"))

    def test_unknown_product_stays_unknown_until_taught(self):
        self.assertIsNone(grocery.section_for(self.conn, "Nutella"))
        grocery.remember_section(self.conn, "Nutella", "snacks", "agent")
        self.assertEqual(grocery.section_for(self.conn, "Nutella"), "snacks")

    def test_learned_section_outlives_the_item(self):
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Nutella"]'), "text"
        )
        grocery.remember_section(self.conn, "Nutella", "snacks", "agent")
        grocery.remove_items(self.conn, "Costco", ["Nutella"])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0], 0
        )
        self.assertEqual(grocery.section_for(self.conn, "Nutella"), "snacks")

    def test_teaching_rejects_an_invented_section(self):
        with self.assertRaises(grocery.GroceryError):
            grocery.remember_section(self.conn, "Nutella", "confectionery")

    def test_layout_changes_the_walk_order(self):
        grocery.ingest_items(
            self.conn,
            "Costco",
            grocery.parse_items_json('["bananas", "detergente", "leite"]'),
            "text",
        )
        _, market = grocery.grouped_items(self.conn, "Costco")
        self.assertEqual([k for k, _ in market], ["produce", "dairy", "household"])

        self.conn.execute("UPDATE stores SET layout = 'warehouse'")
        _, club = grocery.grouped_items(self.conn, "Costco")
        self.assertEqual([k for k, _ in club], ["household", "produce", "dairy"])

    def test_rendered_list_is_messaging_safe(self):
        grocery.ingest_items(
            self.conn,
            "Costco",
            grocery.parse_items_json('[{"name":"leite","quantity":2,"unit":"gal"}]'),
            "text",
        )
        store, groups = grocery.grouped_items(self.conn, "Costco")
        text = grocery.render_grouped(store, groups, "pt")
        self.assertIn("*Laticínios e Ovos*", text)
        self.assertIn("- Leite x2 gal", text)
        # AGENTS.md: no markdown tables and no headers on WhatsApp.
        self.assertNotIn("|", text)
        self.assertNotIn("#", text)

    def test_unclassified_items_fall_into_other(self):
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Nutella"]'), "text"
        )
        _, groups = grocery.grouped_items(self.conn, "Costco")
        self.assertEqual([k for k, _ in groups], ["other"])

    def test_list_reports_when_and_by_whom_it_changed(self):
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Milk"]'), "text",
            actor="owner",
        )
        grocery.set_status(self.conn, "Costco", ["Milk"], "purchased", actor="Kim")
        touched = render.last_touched(self.conn, "Costco")
        self.assertEqual(touched["actor"], "Kim")

        self.assertIn("by Kim", render.render_stamp(touched, "en"))
        self.assertIn("por Kim", render.render_stamp(touched, "pt"))

    def test_stamp_omits_an_unknown_actor(self):
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Milk"]'), "text"
        )
        stamp = render.render_stamp(render.last_touched(self.conn, "Costco"), "en")
        self.assertIn("updated", stamp)
        self.assertNotIn("by", stamp)

    def test_stamp_is_empty_for_an_untouched_store(self):
        self.assertIsNone(render.last_touched(self.conn, "Costco"))
        self.assertEqual(render.render_stamp(None, "en"), "")

    def test_age_is_humanized_in_both_languages(self):
        now = "2026-08-30T12:00:00-04:00"
        cases = [
            ("2026-08-30T11:59:30-04:00", "just now", "agora mesmo"),
            ("2026-08-30T11:40:00-04:00", "20 min ago", "há 20 min"),
            ("2026-08-30T09:00:00-04:00", "3h ago", "há 3h"),
            ("2026-08-28T12:00:00-04:00", "2d ago", "há 2d"),
        ]
        for then, en, pt in cases:
            with self.subTest(then=then):
                self.assertEqual(render.humanize_age(then, "en", now), en)
                self.assertEqual(render.humanize_age(then, "pt", now), pt)

    def test_header_and_confirmations_are_fully_translated(self):
        """A Portuguese reply must not leak English words."""
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["bananas"]'), "text"
        )
        store, groups = grocery.grouped_items(self.conn, "Costco")
        text = grocery.render_grouped(store, groups, "pt")
        for english in ("needed", "updated", "Already bought"):
            self.assertNotIn(english, text)

    def test_reply_language_is_independent_of_the_items(self):
        """Portuguese items answered in English stay English, and vice versa."""
        grocery.ingest_items(
            self.conn,
            "Costco",
            grocery.parse_items_json('["leite", "arroz", "pão"]'),
            "text",
        )
        store, groups = grocery.grouped_items(self.conn, "Costco")

        english = grocery.render_grouped(store, groups, "en")
        self.assertIn("needed", english)
        self.assertIn("*Dairy & Eggs*", english)
        self.assertIn("- Leite", english)      # item name never translated

        portuguese = grocery.render_grouped(store, groups, "pt")
        self.assertIn("na lista", portuguese)
        self.assertIn("*Laticínios e Ovos*", portuguese)
        self.assertIn("- Leite", portuguese)

    def test_confirmations_summarize_rather_than_enumerate(self):
        self.assertEqual(
            render.confirm_items("added", ["Leite", "Bananas", "Arroz"], "pt",
                                 store="Costco"),
            "Adicionados 3 itens em Costco.",
        )
        # A single item is still worth naming: it confirms what was understood.
        self.assertEqual(
            render.confirm_items("added", ["Leite"], "pt", store="Costco"),
            "Adicionado em Costco: Leite.",
        )

    def test_language_follows_the_person_not_the_items(self):
        """A pasted list is its own message: the items cannot pick the language."""
        people.remember_person(self.conn, "owner", "pt", "Jo")
        people.remember_person(self.conn, "kim", "en", "Kim")
        self.assertEqual(people.language_for(self.conn, "owner"), "pt")
        self.assertEqual(people.language_for(self.conn, "kim"), "en")
        # Same Portuguese items; the answer depends only on who is asking.
        self.assertEqual(people.language_for(self.conn, "kim", None), "en")

    def test_explicit_language_beats_a_stored_preference(self):
        people.remember_person(self.conn, "owner", "pt")
        self.assertEqual(people.language_for(self.conn, "owner", "en"), "en")

    def test_unknown_person_gets_the_default(self):
        self.assertEqual(
            people.language_for(self.conn, "+15550001"), people.DEFAULT_LANG
        )
        self.assertEqual(people.language_for(self.conn, None), people.DEFAULT_LANG)

    def test_preference_is_updated_not_duplicated(self):
        people.remember_person(self.conn, "owner", "pt", "Jo")
        people.remember_person(self.conn, "owner", "en", "Jo T")
        rows = self.conn.execute("SELECT * FROM people").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["lang"], rows[0]["display_name"]), ("en", "Jo T"))

    def test_rejects_a_language_it_cannot_render(self):
        with self.assertRaises(grocery.GroceryError):
            people.remember_person(self.conn, "owner", "es")

    def test_stamp_prefers_the_name_they_go_by(self):
        people.remember_person(self.conn, "+15551234567", "pt", "Kim")
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Milk"]'), "text",
            actor="+15551234567",
        )
        stamp = render.render_stamp(render.last_touched(self.conn, "Costco"), "pt")
        self.assertIn("por Kim", stamp)
        self.assertNotIn("+1555", stamp)

    def test_leite_and_milk_are_one_row(self):
        """The bilingual-household bug: one product, two spellings, two rows."""
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["leite", "arroz"]'),
            "text", actor="kim",
        )
        result = grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["milk", "rice"]'),
            "text", actor="owner",
        )
        self.assertEqual(result["added"], [])
        self.assertEqual(sorted(result["merged"]), ["Milk", "Rice"])
        _, rows = grocery.current_items(self.conn, "Costco")
        self.assertEqual(sorted(r["name"] for r in rows), ["Arroz", "Leite"])

    def test_the_first_spelling_is_the_one_kept(self):
        """Whoever wrote it first sees their own words; the key does the matching."""
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["leite"]'), "text"
        )
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Milk"]'), "text"
        )
        _, rows = grocery.current_items(self.conn, "Costco")
        self.assertEqual([r["name"] for r in rows], ["Leite"])

    def test_either_spelling_finds_the_item(self):
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["leite"]'), "text"
        )
        result = grocery.set_status(self.conn, "Costco", ["milk"], "purchased")
        self.assertEqual(result["items"], ["Leite"])

    def test_qualified_products_stay_separate(self):
        """leite de coco is not leite; an over-merge would replace a real item."""
        grocery.ingest_items(
            self.conn,
            "Costco",
            grocery.parse_items_json('["leite", "leite de coco", "coconut milk"]'),
            "text",
        )
        _, rows = grocery.current_items(self.conn, "Costco")
        self.assertEqual(len(rows), 2)
        self.assertIn("Leite", [r["name"] for r in rows])

    def test_units_still_separate_the_same_product(self):
        grocery.ingest_items(
            self.conn,
            "Costco",
            grocery.parse_items_json(
                '[{"name":"leite","unit":"gal"}, {"name":"milk","unit":"L"}]'
            ),
            "text",
        )
        _, rows = grocery.current_items(self.conn, "Costco")
        self.assertEqual(sorted(r["unit"] for r in rows), ["gal", "l"])

    def test_a_persons_own_store_beats_household_activity(self):
        """A stated preference outranks an inference from someone else's trip."""
        people.remember_person(self.conn, "sam", "pt", "Sam", "Food Bazaar")
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Milk"]'), "text",
            actor="owner",
        )
        name, assumption = grocery.resolve_store(self.conn, None, None, "sam")
        self.assertEqual(name, "Food Bazaar")
        self.assertEqual(assumption["kind"], "store_personal_default")
        self.assertIn("você definiu", render.render_assumption(assumption, "pt"))

        # Someone with no preference still falls back to the last store touched.
        other, other_assumption = grocery.resolve_store(self.conn, None, None, "owner")
        self.assertEqual(other, "Costco")
        self.assertEqual(other_assumption["kind"], "store_last_touched")

    def test_an_explicit_store_still_wins(self):
        people.remember_person(self.conn, "sam", "pt", "Sam", "Food Bazaar")
        self.assertEqual(
            grocery.resolve_store(self.conn, "Costco", None, "sam"), ("Costco", None)
        )

    def test_a_roster_sync_does_not_wipe_a_chosen_store(self):
        people.remember_person(self.conn, "sam", "pt", "Sam", "Food Bazaar")
        people.remember_person(self.conn, "sam", "pt", "Sam")   # as roster sync does
        self.assertEqual(people.default_store_for(self.conn, "sam"), "Food Bazaar")

    def test_buying_an_unlisted_item_still_gets_a_dedup_key(self):
        """That insert path omitted canonical_name, so those rows merged with
        nothing — the one language bug reachable straight from WhatsApp."""
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Pão"]'), "text"
        )
        grocery.set_status(self.conn, "Costco", ["leite"], "purchased")
        row = self.conn.execute(
            "SELECT canonical_name FROM items WHERE name = 'Leite'"
        ).fetchone()
        self.assertEqual(row["canonical_name"], "milk")

        # ...so the English spelling merges instead of making a second row.
        result = grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["milk"]'), "text"
        )
        self.assertEqual(result["added"], [])
        _, rows = grocery.current_items(self.conn, "Costco")
        self.assertEqual(sorted(r["name"] for r in rows), ["Leite", "Pão"])

    def test_the_upgrade_repairs_a_blank_dedup_key(self):
        """Rows written by an engine that left the key blank are fixed when the
        database is upgraded. Since opens are gated on the schema version, this
        is a one-time repair — which is why no current path may write a blank
        key (see test_every_items_insert_sets_a_dedup_key)."""
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["leite"]'), "text"
        )
        self.conn.execute("UPDATE items SET canonical_name = ''")
        self.conn.execute("PRAGMA user_version = 0")    # as the previous engine left it
        self.conn.commit()
        self.conn.close()
        self.conn = grocery.connect(self.db)
        self.assertEqual(
            self.conn.execute("SELECT canonical_name FROM items").fetchone()[0],
            "milk",
        )

    def test_a_reopened_item_still_merges_across_languages(self):
        """QA N1: reopen re-inserted bought items with a blank dedup key, so
        adding "milk" afterwards made a second row beside "Leite"."""
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Leite", "Pão"]'), "text"
        )
        grocery.set_status(self.conn, "Costco", ["Leite"], "purchased")
        grocery.close_trip(self.conn, "Costco")        # deletes the bought Leite row
        grocery.reopen_trip(self.conn, "Costco")       # and inserts it again
        result = grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["milk"]'), "text"
        )
        self.assertEqual((result["added"], result["merged"]), ([], ["Milk"]))
        rows = self.conn.execute(
            "SELECT name, canonical_name FROM items ORDER BY name").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("Leite", "milk"), ("Pão", "bread")])

    def test_a_reopen_finds_the_row_under_its_other_language_name(self):
        """The trip archived "Leite"; the list meanwhile holds "Milk". Reopen
        must restore onto that row, not insert a second one."""
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Leite", "Pão"]'), "text"
        )
        grocery.set_status(self.conn, "Costco", ["Leite"], "purchased")
        grocery.close_trip(self.conn, "Costco")
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["milk"]'), "text"
        )
        grocery.reopen_trip(self.conn, "Costco")
        names = [r["name"] for r in self.conn.execute("SELECT name FROM items ORDER BY name")]
        self.assertEqual(names, ["Milk", "Pão"])

    def test_every_items_insert_sets_a_dedup_key(self):
        """Static guard: every INSERT INTO items in the engine names
        canonical_name. A blank key silently stops cross-language merging, and
        the upgrade that repairs one runs only once."""
        import re
        app = Path(db.__file__).resolve().parent
        found = 0
        for source in sorted(app.glob("*.py")):
            if source.name.startswith("test_"):
                continue
            text = source.read_text(encoding="utf-8")
            for match in re.finditer(r"INSERT\s+INTO\s+items\s*\((.*?)\)", text, re.S):
                found += 1
                with self.subTest(file=source.name, at=text[:match.start()].count("\n") + 1):
                    self.assertIn("canonical_name", match.group(1))
        self.assertGreaterEqual(found, 3, "expected ingest, buy-unlisted and reopen")

    def test_non_finite_quantities_are_refused(self):
        """QA M2: nan slipped past `quantity <= 0` and died as an IntegrityError."""
        for quantity in ("inf", "nan", "Infinity", "1e999"):
            with self.subTest(quantity=quantity), self.assertRaises(grocery.GroceryError):
                grocery.parse_items_json(json.dumps([{"name": "Milk", "quantity": quantity}]))
        with self.assertRaises(grocery.GroceryError):
            grocery.parse_items_json('[{"name": "Milk", "quantity": NaN}]')

    def test_share_requires_active_allowlist_entry(self):
        grocery.ingest_items(
            self.conn, "Trader Joe's", grocery.parse_items_json('["Bananas"]'), "text"
        )
        with self.assertRaises(grocery.GroceryError):
            grocery.prepare_share(self.conn, "Trader Joe's", "Mom")
        grocery.add_contact(self.conn, "Mom", "whatsapp", "+15551234567")
        share = grocery.prepare_share(self.conn, "Trader Joe's", "Mom")
        self.assertFalse(share["delivered"])
        self.assertIn("Bananas", share["payload"])
        grocery.remove_contact(self.conn, "Mom")
        with self.assertRaises(grocery.GroceryError):
            grocery.prepare_share(self.conn, "Trader Joe's", "Mom")


if __name__ == "__main__":
    unittest.main()

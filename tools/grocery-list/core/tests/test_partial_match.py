"""`buy` with part of a pending item's name.

Regression: "Comprei bounty" against a pending "Paper towels (Bounty)" was
recorded as an unlisted "Bounty", and the agent's retry with the full name left
a spurious second purchase. A partial name now reaches the one pending item it
names, or comes back as a choice when it could name several, or when a
synonyms guard says it names a different product ("leite" vs "Leite
condensado").
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import cli
import grocery
import render


class PartialMatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "test.sqlite3"
        self.conn = grocery.connect(self.path)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def stock(self, *names, store="Food Bazaar"):
        grocery.ingest_items(self.conn, store,
                             grocery.parse_items_json(json.dumps(list(names))), "text")

    def buy(self, *names, store="Food Bazaar", unit=None):
        return grocery.set_status(self.conn, store, list(names), "purchased", unit)

    def statuses(self, store="Food Bazaar"):
        _, rows = grocery.current_items(self.conn, store)
        return {r["name"]: r["status"] for r in rows}

    def events(self, action):
        return [r[0] for r in self.conn.execute(
            "SELECT item_name FROM events WHERE action = ? ORDER BY id", (action,))]

    def assertPicked(self, spoken, item, result):
        self.assertEqual(result["items"], [item])
        self.assertEqual(result["ambiguous"], [])
        self.assertEqual(result["assumptions"],
                         [{"kind": "item_partial_match", "name": spoken, "item": item}])

    def assertChoice(self, spoken, candidates, result):
        self.assertEqual(result["items"], [])
        self.assertEqual(result["ambiguous"],
                         [{"name": spoken, "candidates": candidates}])
        self.assertEqual(result["assumptions"][0]["kind"], "item_choice_needed")

    # -- picks the one item ------------------------------------------------

    def test_brand_parenthetical(self):
        self.stock("Paper towels (Bounty)", "Milk")
        result = self.buy("Bounty")
        self.assertPicked("Bounty", "Paper towels (Bounty)", result)
        self.assertEqual(self.statuses(),
                         {"Paper towels (Bounty)": "purchased", "Milk": "needed"})
        # One purchase, of the listed item; no invented "Bounty" row.
        self.assertEqual(self.events("purchased"), ["Paper towels (Bounty)"])
        self.assertEqual(self.events("added"), ["Paper towels (Bounty)", "Milk"])
        self.assertEqual(
            render.render_assumption(result["assumptions"][0], "pt"),
            "entendi Bounty como Paper towels (Bounty)",
        )

    def test_brand_is_case_insensitive(self):
        self.stock("Paper towels (Bounty)")
        self.assertPicked("Bounty", "Paper towels (Bounty)", self.buy("bounty"))

    def test_single_whole_token(self):
        self.stock("Bounty paper towels", "Eggs")
        self.assertPicked("Bounty", "Bounty paper towels", self.buy("bounty"))
        self.assertEqual(self.statuses()["Eggs"], "needed")

    def test_head_noun_that_is_not_a_curated_product(self):
        self.stock("Requeijão cremoso")
        self.assertPicked("Requeijao", "Requeijão cremoso", self.buy("requeijao"))

    def test_pt_accents_both_ways(self):
        self.stock("Açúcar mascavo (União)", "Cafe Pilao")
        self.assertPicked("Uniao", "Açúcar mascavo (União)", self.buy("uniao"))
        self.assertPicked("Pilão", "Cafe Pilao", self.buy("Pilão"))

    def test_cross_language_name_without_its_brand(self):
        # "papel toalha" and "paper towels" share a canonical key.
        self.stock("Paper towels (Bounty)")
        self.assertPicked("Papel toalha", "Paper towels (Bounty)",
                          self.buy("papel toalha"))

    def test_same_item_in_two_units_still_breaks_the_tie(self):
        grocery.ingest_items(self.conn, "Food Bazaar", grocery.parse_items_json(
            '[{"name":"Paper towels (Bounty)","unit":"roll"},'
            ' {"name":"Paper towels (Bounty)","unit":"pack"}]'), "text")
        result = self.buy("Bounty")
        self.assertEqual(result["items"], ["Paper towels (Bounty)"])
        self.assertEqual([a["kind"] for a in result["assumptions"]],
                         ["item_partial_match", "item_ambiguous"])

    # -- asks instead of guessing ------------------------------------------

    def test_several_candidates_change_nothing(self):
        self.stock("Paper towels (Bounty)", "Bounty napkins")
        result = self.buy("Bounty")
        self.assertChoice("Bounty", ["Bounty napkins", "Paper towels (Bounty)"], result)
        self.assertEqual(set(self.statuses().values()), {"needed"})
        self.assertEqual(self.events("purchased"), [])
        self.assertEqual(
            render.render_assumption(result["assumptions"][0], "en"),
            "Bounty could be Bounty napkins, Paper towels (Bounty); "
            "marked nothing, which one?",
        )

    def test_other_names_in_the_same_call_still_apply(self):
        self.stock("Paper towels (Bounty)", "Bounty napkins", "Milk")
        result = self.buy("Bounty", "Milk")
        self.assertEqual(result["items"], ["Milk"])
        self.assertEqual(len(result["ambiguous"]), 1)
        self.assertEqual(self.statuses()["Milk"], "purchased")

    def test_leite_does_not_silently_become_leite_condensado(self):
        self.stock("Leite condensado")
        result = self.buy("leite")
        self.assertChoice("Leite", ["Leite condensado"], result)
        self.assertEqual(self.statuses(), {"Leite condensado": "needed"})

    def test_exact_leite_still_wins_over_condensado(self):
        self.stock("Leite", "Leite condensado")
        result = self.buy("leite")
        self.assertEqual(result, {"items": ["Leite"], "assumptions": [], "ambiguous": []})
        self.assertEqual(self.statuses()["Leite condensado"], "needed")

    def test_other_qualified_products_are_guarded(self):
        for listed, spoken in (("Creme de leite", "leite"),
                               ("Batata doce", "batata"),
                               ("Iogurte (morango)", "morango"),
                               ("Tomates italianos", "tomate")):
            with self.subTest(listed=listed):
                self.stock(listed, store=listed)
                result = self.buy(spoken, store=listed)
                self.assertEqual(result["ambiguous"][0]["candidates"], [listed])
                self.assertEqual(self.statuses(listed), {listed: "needed"})

    def test_cross_language_token_is_guarded(self):
        # "leite" is "milk", but "Whole milk" is its own product.
        self.stock("Whole milk")
        self.assertChoice("Leite", ["Whole milk"], self.buy("leite"))

    # -- unchanged behaviour -----------------------------------------------

    def test_no_match_is_still_bought_off_list(self):
        self.stock("Paper towels (Bounty)")
        result = self.buy("Cilantro")
        self.assertEqual(result["assumptions"],
                         [{"kind": "item_not_listed", "name": "Cilantro"}])
        self.assertEqual(result["ambiguous"], [])
        self.assertEqual(self.statuses()["Paper towels (Bounty)"], "needed")

    def test_part_of_a_word_is_not_a_token(self):
        self.stock("Bountiful snacks")
        result = self.buy("Bounty")
        self.assertEqual(result["assumptions"][0]["kind"], "item_not_listed")

    def test_only_pending_items_in_the_same_store(self):
        self.stock("Paper towels (Bounty)", store="Costco")
        self.stock("Milk")
        result = self.buy("Bounty")
        self.assertEqual(result["assumptions"][0]["kind"], "item_not_listed")
        self.assertEqual(self.statuses("Costco"), {"Paper towels (Bounty)": "needed"})

    def test_unit_filter_applies(self):
        grocery.ingest_items(self.conn, "Food Bazaar", grocery.parse_items_json(
            '[{"name":"Paper towels (Bounty)","unit":"roll"}]'), "text")
        result = self.buy("Bounty", unit="pack")
        self.assertEqual(result["assumptions"][0]["kind"], "item_not_listed")

    def test_unbuy_and_remove_do_not_guess(self):
        self.stock("Paper towels (Bounty)")
        self.buy("Paper towels (Bounty)")
        with self.assertRaises(grocery.GroceryError):
            grocery.set_status(self.conn, "Food Bazaar", ["Bounty"], "needed")
        with self.assertRaises(grocery.GroceryError):
            grocery.remove_items(self.conn, "Food Bazaar", ["Bounty"])

    # -- what the plugin receives ------------------------------------------

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.run(["--db", str(self.path), *argv])
        return out.getvalue()

    def test_json_carries_candidates_and_a_rendered_clause(self):
        self.stock("Paper towels (Bounty)", "Bounty napkins")
        payload = json.loads(self.run_cli("buy", "--store", "Food Bazaar",
                                          "Bounty", "--lang", "pt"))
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["ambiguous"], [
            {"name": "Bounty", "candidates": ["Bounty napkins", "Paper towels (Bounty)"]},
        ])
        self.assertEqual(payload["assumptions"], [
            "Bounty pode ser Bounty napkins, Paper towels (Bounty); não marquei nada, qual é?",
        ])

    def test_json_reports_the_picked_item(self):
        self.stock("Paper towels (Bounty)")
        payload = json.loads(self.run_cli("buy", "--store", "Food Bazaar", "Bounty"))
        self.assertEqual(payload["items"], ["Paper towels (Bounty)"])
        self.assertEqual(payload["ambiguous"], [])
        self.assertEqual(payload["assumptions"],
                         ["took Bounty as Paper towels (Bounty)"])


if __name__ == "__main__":
    unittest.main()

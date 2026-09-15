#!/usr/bin/env python3
"""Tests for the bilingual product -> store section classifier."""

import unittest

import sections


class SectionMetadataTests(unittest.TestCase):
    def test_every_section_has_both_labels(self):
        for key in sections.SECTION_KEYS:
            with self.subTest(section=key):
                labels = sections.SECTIONS[key]
                self.assertEqual(set(labels), {"en", "pt"})
                self.assertTrue(labels["en"].strip())
                self.assertTrue(labels["pt"].strip())

    def test_keys_are_stable_machine_keys(self):
        for key in sections.SECTION_KEYS:
            with self.subTest(section=key):
                self.assertRegex(key, r"^[a-z][a-z_]*$")
        self.assertIn(sections.DEFAULT_SECTION, sections.SECTIONS)

    def test_label_in_both_languages(self):
        self.assertEqual(sections.label("produce", "en"), "Produce")
        self.assertEqual(sections.label("produce", "pt"), "Hortifrúti")
        self.assertEqual(sections.label("butcher", "pt"), "Açougue")
        self.assertEqual(sections.label("bakery", "pt"), "Padaria")
        self.assertEqual(sections.label("frozen", "pt"), "Congelados")

    def test_label_defaults_to_english(self):
        self.assertEqual(sections.label("pantry"), "Pantry")

    def test_label_accepts_locale_spellings(self):
        for lang in ("pt", "pt-BR", "pt_br", "PT", "portuguese"):
            with self.subTest(lang=lang):
                self.assertEqual(sections.label("dairy", lang), sections.label("dairy", "pt"))
        for lang in ("en", "en-US", "EN", "english"):
            with self.subTest(lang=lang):
                self.assertEqual(sections.label("dairy", lang), sections.label("dairy", "en"))

    def test_label_rejects_unknown_section_and_language(self):
        with self.assertRaises(KeyError):
            sections.label("aisle_9", "en")
        with self.assertRaises(ValueError):
            sections.label("produce", "fr")

    def test_section_list_is_store_walk_order(self):
        listing = sections.section_list("pt")
        self.assertEqual([key for key, _ in listing], list(sections.SECTION_KEYS))
        self.assertEqual(dict(listing)["seafood"], "Peixaria")


class EnglishTests(unittest.TestCase):
    CASES = {
        "milk": "dairy",
        "cheese": "dairy",
        "eggs": "dairy",
        "chicken": "butcher",
        "ground beef": "butcher",
        "bacon": "butcher",
        "shrimp": "seafood",
        "salmon": "seafood",
        "banana": "produce",
        "lettuce": "produce",
        "sweet potato": "produce",
        "bread": "bakery",
        "croissant": "bakery",
        "rice": "pantry",
        "olive oil": "pantry",
        "black pepper": "pantry",
        "beer": "beverages",
        "sparkling water": "beverages",
        "potato chips": "snacks",
        "chocolate": "snacks",
        "ice cream": "frozen",
        "detergent": "household",
        "toilet paper": "household",
        "toothpaste": "personal_care",
        "shampoo": "personal_care",
        "dog food": "pet",
        "diapers": "baby",
    }

    def test_english_terms(self):
        for name, expected in self.CASES.items():
            with self.subTest(name=name):
                self.assertEqual(sections.classify(name), expected)


class PortugueseTests(unittest.TestCase):
    CASES = {
        "leite": "dairy",
        "queijo": "dairy",
        "ovo": "dairy",
        "frango": "butcher",
        "carne moída": "butcher",
        "linguiça": "butcher",
        "camarão": "seafood",
        "bacalhau": "seafood",
        "banana": "produce",
        "alface": "produce",
        "cebola": "produce",
        "pão": "bakery",
        "bolo": "bakery",
        "arroz": "pantry",
        "feijão": "pantry",
        "açúcar": "pantry",
        "café": "pantry",
        "cerveja": "beverages",
        "refrigerante": "beverages",
        "salgadinho": "snacks",
        "biscoito": "snacks",
        "sorvete": "frozen",
        "detergente": "household",
        "papel higiênico": "household",
        "sabonete": "personal_care",
        "desodorante": "personal_care",
        "ração": "pet",
        "fralda": "baby",
    }

    def test_portuguese_terms(self):
        for name, expected in self.CASES.items():
            with self.subTest(name=name):
                self.assertEqual(sections.classify(name), expected)

    def test_cross_language_pairs_agree(self):
        pairs = [
            ("milk", "leite"),
            ("chicken", "frango"),
            ("rice", "arroz"),
            ("bread", "pão"),
            ("fish", "peixe"),
            ("beer", "cerveja"),
            ("detergent", "detergente"),
            ("shampoo", "xampu"),
            ("diaper", "fralda"),
            ("cat food", "ração de gato"),
        ]
        for english, portuguese in pairs:
            with self.subTest(pair=(english, portuguese)):
                self.assertEqual(sections.classify(english), sections.classify(portuguese))


class AccentTests(unittest.TestCase):
    PAIRS = [
        ("pão", "pao"),
        ("maçã", "maca"),
        ("açúcar", "acucar"),
        ("limão", "limao"),
        ("água", "agua"),
        ("ração", "racao"),
        ("hortifrúti", "hortifruti"),
        ("café", "cafe"),
        ("linguiça", "linguica"),
        ("papel higiênico", "papel higienico"),
    ]

    def test_accented_and_unaccented_match(self):
        for accented, plain in self.PAIRS:
            with self.subTest(term=accented):
                self.assertIsNotNone(sections.classify(accented))
                self.assertEqual(sections.classify(accented), sections.classify(plain))

    def test_case_is_ignored(self):
        for name in ("LEITE", "Leite", "lEiTe", "PÃO DE QUEIJO"):
            with self.subTest(name=name):
                self.assertEqual(sections.classify(name), sections.classify(name.lower()))


class PluralTests(unittest.TestCase):
    CASES = {
        "bananas": "produce",
        "tomatoes": "produce",
        "potatoes": "produce",
        "eggs": "dairy",
        "cookies": "snacks",
        "onions": "produce",
        "tomates": "produce",
        "ovos": "dairy",
        "limões": "produce",
        "pães": "bakery",
        "feijões": "pantry",
        "pimentões": "produce",
        "maçãs": "produce",
        "biscoitos": "snacks",
        "detergentes": "household",
        "pilhas": "household",
    }

    def test_plurals_resolve_to_the_singular_term(self):
        for name, expected in self.CASES.items():
            with self.subTest(name=name):
                self.assertEqual(sections.classify(name), expected)


class MultiWordTests(unittest.TestCase):
    def test_head_noun_wins_in_portuguese(self):
        self.assertEqual(sections.classify("leite integral"), "dairy")
        self.assertEqual(sections.classify("leite desnatado"), "dairy")
        self.assertEqual(sections.classify("peito de frango"), "butcher")
        self.assertEqual(sections.classify("arroz integral"), "pantry")
        self.assertEqual(sections.classify("suco de laranja"), "beverages")
        self.assertEqual(sections.classify("iogurte natural"), "dairy")

    def test_head_noun_wins_in_english(self):
        self.assertEqual(sections.classify("whole milk"), "dairy")
        self.assertEqual(sections.classify("chicken breast"), "butcher")
        self.assertEqual(sections.classify("apple juice"), "beverages")
        self.assertEqual(sections.classify("chocolate milk"), "dairy")
        self.assertEqual(sections.classify("brown rice"), "pantry")

    def test_listed_phrase_beats_its_parts(self):
        self.assertEqual(sections.classify("leite"), "dairy")
        self.assertEqual(sections.classify("leite de coco"), "pantry")
        self.assertEqual(sections.classify("coconut milk"), "pantry")
        self.assertEqual(sections.classify("soap"), "personal_care")
        self.assertEqual(sections.classify("dish soap"), "household")
        self.assertEqual(sections.classify("chá"), "pantry")
        self.assertEqual(sections.classify("chá gelado"), "beverages")
        self.assertEqual(sections.classify("atum"), "seafood")
        self.assertEqual(sections.classify("atum em lata"), "pantry")
        self.assertEqual(sections.classify("cereal"), "pantry")
        self.assertEqual(sections.classify("barra de cereal"), "snacks")
        self.assertEqual(sections.classify("água"), "beverages")
        self.assertEqual(sections.classify("água sanitária"), "household")
        self.assertEqual(sections.classify("manteiga"), "dairy")
        self.assertEqual(sections.classify("papel manteiga"), "household")
        self.assertEqual(sections.classify("batata"), "produce")
        self.assertEqual(sections.classify("batata palha"), "snacks")

    def test_connector_may_be_omitted(self):
        self.assertEqual(sections.classify("leite coco"), "pantry")
        self.assertEqual(sections.classify("papel higienico"), "household")

    def test_frozen_marker_wins(self):
        self.assertEqual(sections.classify("batata frita congelada"), "frozen")
        self.assertEqual(sections.classify("frozen peas"), "frozen")
        self.assertEqual(sections.classify("pizza congelada"), "frozen")

    def test_quantity_and_unit_noise_is_ignored(self):
        for name in ("2 kg de arroz", "arroz 5kg", "- Arroz", "arroz, 1 pct"):
            with self.subTest(name=name):
                self.assertEqual(sections.classify(name), "pantry")
        self.assertEqual(sections.classify("Leite integral 1L"), "dairy")


class UnknownTests(unittest.TestCase):
    UNKNOWN = [
        "parafuso",
        "chave de fenda",
        "guarda-chuva",
        "fita métrica",
        "carregador de celular",
        "screwdriver",
        "extension cord",
        "quantum flux capacitor",
        "widget",
        "asdfghjkl",
        "salsa",
    ]

    def test_unknown_products_return_none(self):
        for name in self.UNKNOWN:
            with self.subTest(name=name):
                self.assertIsNone(sections.classify(name))

    def test_empty_input_returns_none(self):
        for name in ("", "   ", "-", "123", "2 kg", None):
            with self.subTest(name=name):
                self.assertIsNone(sections.classify(name))

    def test_classifier_never_guesses_other(self):
        for name in self.UNKNOWN:
            with self.subTest(name=name):
                self.assertNotEqual(sections.classify(name), sections.DEFAULT_SECTION)


class IndexIntegrityTests(unittest.TestCase):
    def test_every_result_is_a_known_section(self):
        for term in sections._INDEX:
            with self.subTest(term=term):
                result = sections.classify(term)
                self.assertIn(result, sections.SECTION_KEYS)
                self.assertNotEqual(result, sections.DEFAULT_SECTION)

    def test_terms_are_stored_normalized(self):
        for term in sections._INDEX:
            with self.subTest(term=term):
                self.assertTrue(term)
                self.assertEqual(sections.fold(term), term)

    def test_both_languages_are_covered_for_every_real_section(self):
        for key, groups in sections.KEYWORDS.items():
            if key == sections.DEFAULT_SECTION:
                continue
            with self.subTest(section=key):
                self.assertTrue(groups["en"], f"{key} has no English terms")
                self.assertTrue(groups["pt"], f"{key} has no Portuguese terms")

    def test_map_is_meaningfully_sized(self):
        self.assertGreater(sections.term_count(), 400)

    def test_fold_is_idempotent(self):
        for name in ("Leite Integral", "PÃO DE QUEIJO", "- Açúcar, 1kg"):
            with self.subTest(name=name):
                once = sections.fold(name)
                self.assertEqual(sections.fold(once), once)


if __name__ == "__main__":
    unittest.main()

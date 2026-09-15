#!/usr/bin/env python3
"""Tests for the bilingual grocery product synonym map."""

import unittest

import sections
import synonyms


class MapIntegrityTests(unittest.TestCase):
    def test_keys_are_folded_and_belong_to_their_own_group(self):
        for key, terms in synonyms.GROUPS.items():
            with self.subTest(group=key):
                self.assertEqual(key, synonyms.fold(key))
                folded = {synonyms.fold(term) for term in terms}
                self.assertIn(key, folded)

    def test_every_group_has_something_to_merge(self):
        for key, terms in synonyms.GROUPS.items():
            with self.subTest(group=key):
                self.assertGreaterEqual(len(terms), 2)
                self.assertGreaterEqual(len({synonyms.fold(t) for t in terms}), 2)

    def test_no_term_is_claimed_by_two_groups(self):
        owner = {}
        for key, terms in synonyms.GROUPS.items():
            for term in terms:
                folded = synonyms.fold(term)
                previous = owner.setdefault(folded, key)
                self.assertEqual(previous, key, f"{folded!r} claimed twice")

    def test_canonical_keys_match_group_order(self):
        self.assertEqual(synonyms.CANONICAL_KEYS, tuple(synonyms.GROUPS))

    def test_coverage_is_reported_honestly(self):
        self.assertEqual(synonyms.group_count(), len(synonyms.GROUPS))
        self.assertGreater(synonyms.term_count(), synonyms.group_count())

    def test_every_listed_term_reaches_its_own_key(self):
        for key, terms in synonyms.GROUPS.items():
            for term in terms:
                with self.subTest(term=term):
                    self.assertEqual(synonyms.canonical(term), key)


class FoldTests(unittest.TestCase):
    SAMPLES = (
        "Leite", "MAÇÃ", "  pão   de   forma ", "Açúcar", "- arroz,",
        "Papel Higiênico", "2 kg de arroz", "café",
    )

    def test_fold_matches_the_sibling_module(self):
        # sections.py and synonyms.py must normalize identically or a name
        # keyed in one will not line up in the other.
        for sample in self.SAMPLES:
            with self.subTest(sample=sample):
                self.assertEqual(synonyms.fold(sample), sections.fold(sample))

    def test_fold_strips_accents_and_punctuation(self):
        self.assertEqual(synonyms.fold("Maçã"), "maca")
        self.assertEqual(synonyms.fold("PÃO"), "pao")
        self.assertEqual(synonyms.fold("- Açúcar,"), "acucar")


class CrossLanguageTests(unittest.TestCase):
    PAIRS = (
        ("leite", "milk"),
        ("arroz", "rice"),
        ("frango", "chicken"),
        ("pão", "bread"),
        ("pão sourdough", "sourdough bread"),
        ("ovos", "eggs"),
        ("queijo", "cheese"),
        ("manteiga", "butter"),
        ("açúcar", "sugar"),
        ("café", "coffee"),
        ("batata", "potato"),
        ("cebola", "onion"),
        ("alho", "garlic"),
        ("tomate", "tomato"),
        ("maçã", "apple"),
        ("feijão", "beans"),
        ("cenoura", "carrot"),
        ("detergente", "dish soap"),
        ("papel higiênico", "toilet paper"),
        ("sabão em pó", "laundry detergent"),
        ("amaciante", "fabric softener"),
        ("papel alumínio", "aluminum foil"),
        ("saco de lixo", "trash bag"),
        ("fralda", "diaper"),
        ("ração de gato", "cat food"),
        ("pasta de dente", "toothpaste"),
        ("escova de dente", "toothbrush"),
        ("protetor solar", "sunscreen"),
        ("carne moída", "ground beef"),
        ("peito de frango", "chicken breast"),
        ("água com gás", "sparkling water"),
        ("suco de laranja", "orange juice"),
        ("azeite", "olive oil"),
        ("farinha de trigo", "wheat flour"),
        ("leite condensado", "condensed milk"),
        ("sorvete", "ice cream"),
        ("cerveja", "beer"),
        ("guardanapo", "napkin"),
    )

    def test_pairs_share_one_canonical_key(self):
        for pt, en in self.PAIRS:
            with self.subTest(pt=pt, en=en):
                self.assertEqual(synonyms.canonical(pt), synonyms.canonical(en))
                self.assertTrue(synonyms.same_product(pt, en))
                self.assertTrue(synonyms.same_product(en, pt))

    def test_distinct_products_keep_distinct_keys(self):
        distinct = ("leite", "arroz", "frango", "pão", "queijo", "detergente")
        keys = [synonyms.canonical(name) for name in distinct]
        self.assertEqual(len(set(keys)), len(distinct))


class CaseAndAccentTests(unittest.TestCase):
    def test_case_and_accent_variants_agree(self):
        for spelling in ("Leite", "leite", "LEITE", "  leite ", "milk", "Milk", "MILK", "milks"):
            with self.subTest(spelling=spelling):
                self.assertEqual(synonyms.canonical(spelling), "milk")

    def test_unaccented_typing_still_matches(self):
        for typed, accented in (
            ("maca", "maçã"),
            ("acucar", "açúcar"),
            ("pao", "pão"),
            ("pao sourdough", "pão sourdough"),
            ("cafe", "café"),
            ("limao", "limão"),
            ("feijao", "feijão"),
            ("papel higienico", "papel higiênico"),
            ("sabao em po", "sabão em pó"),
        ):
            with self.subTest(typed=typed):
                self.assertEqual(synonyms.canonical(typed), synonyms.canonical(accented))


class PluralTests(unittest.TestCase):
    CASES = {
        "bananas": "banana",
        "banana": "banana",
        "ovos": "egg",
        "eggs": "egg",
        "egg": "egg",
        "tomatoes": "tomato",
        "tomates": "tomato",
        "potatoes": "potato",
        "batatas": "potato",
        "limões": "lime",
        "pães": "bread",
        "maçãs": "apple",
        "apples": "apple",
        "cookies": "cookie",
        "cebolas": "onion",
        "fraldas": "diaper",
        "diapers": "diaper",
    }

    def test_plurals_reach_the_singular_key(self):
        for name, key in self.CASES.items():
            with self.subTest(name=name):
                self.assertEqual(synonyms.canonical(name), key)

    def test_plural_crosses_languages(self):
        self.assertTrue(synonyms.same_product("ovos", "eggs"))
        self.assertTrue(synonyms.same_product("bananas", "banana"))
        self.assertTrue(synonyms.same_product("tomates", "tomatoes"))


class RegionalPortugueseTests(unittest.TestCase):
    FAMILIES = (
        ("mandioca", "aipim", "macaxeira", "cassava"),
        ("bergamota", "mexerica", "tangerina", "poncã", "tangerine", "mandarin"),
        ("abobrinha", "courgette", "zucchini"),
        ("biscoito", "bolacha", "cookie"),
        ("muçarela", "mussarela", "mozzarella"),
        ("xampu", "shampoo"),
        ("pão francês", "pãozinho", "pão de sal"),
        ("gilete", "aparelho de barbear", "razor"),
    )

    def test_regional_spellings_collapse_to_one_key(self):
        for family in self.FAMILIES:
            keys = {synonyms.canonical(name) for name in family}
            with self.subTest(family=family):
                self.assertEqual(len(keys), 1, f"{family} split into {keys}")


class QuantityNoiseTests(unittest.TestCase):
    def test_quantities_and_units_do_not_block_a_match(self):
        for name in ("2 kg de arroz", "1kg arroz", "arroz 5 kg", "5kg de arroz"):
            with self.subTest(name=name):
                self.assertEqual(synonyms.canonical(name), "rice")

    def test_spelled_out_measures_are_noise_too(self):
        self.assertEqual(synonyms.canonical("dozen eggs"), "egg")
        self.assertEqual(synonyms.canonical("2 litros de leite"), "milk")


class QualifiedPhraseTests(unittest.TestCase):
    """The main correctness risk: a qualified name must not collapse to its head.

    A false merge silently destroys a list entry, so every one of these pairs
    has to stay apart.
    """

    MUST_NOT_MERGE = (
        ("leite de coco", "leite"),
        ("coconut milk", "milk"),
        ("leite de coco", "milk"),
        ("coconut milk", "leite"),
        ("creme de leite", "leite"),
        ("creme de leite", "milk"),
        ("leite condensado", "leite"),
        ("leite em pó", "leite"),
        ("condensed milk", "milk"),
        ("batata doce", "batata"),
        ("sweet potato", "potato"),
        ("batata frita", "batata"),
        ("french fries", "potato"),
        ("peito de frango", "frango"),
        ("chicken breast", "chicken"),
        ("caldo de galinha", "frango"),
        ("pasta de dente", "macarrão"),
        ("pasta de amendoim", "pasta"),
        ("peanut butter", "butter"),
        ("manteiga de amendoim", "manteiga"),
        ("papel higiênico", "papel toalha"),
        ("toilet paper", "paper towel"),
        ("água de coco", "água"),
        ("coconut water", "water"),
        ("água sanitária", "água"),
        ("suco de laranja", "suco"),
        ("orange juice", "juice"),
        ("suco de laranja", "laranja"),
        ("milho de pipoca", "milho"),
        ("farinha de rosca", "farinha"),
        ("farinha de trigo", "farinha"),
        ("farinha de mandioca", "mandioca"),
        ("feijão preto", "feijão"),
        ("black beans", "beans"),
        ("molho de tomate", "tomate"),
        ("extrato de tomate", "molho de tomate"),
        ("arroz integral", "arroz"),
        ("brown rice", "rice"),
        ("sabão em pó", "sabonete"),
        ("laundry detergent", "dish soap"),
        ("ração de gato", "ração de cachorro"),
        ("cat food", "dog food"),
        ("queijo ralado", "queijo"),
        ("pimenta do reino", "pimentão"),
        ("black pepper", "bell pepper"),
        ("limão siciliano", "limão"),
        ("lemon", "lime"),
        ("carne moída", "carne"),
        ("carne de porco", "carne"),
        ("chá gelado", "chá"),
        ("iced tea", "tea"),
    )

    def test_qualified_names_do_not_collapse_into_their_head_noun(self):
        for qualified, head in self.MUST_NOT_MERGE:
            with self.subTest(qualified=qualified, head=head):
                self.assertNotEqual(
                    synonyms.canonical(qualified),
                    synonyms.canonical(head),
                    f"{qualified!r} wrongly merged with {head!r}",
                )
                self.assertFalse(synonyms.same_product(qualified, head))

    def test_qualified_pairs_still_merge_across_languages(self):
        for pt, en in (
            ("leite de coco", "coconut milk"),
            ("batata doce", "sweet potato"),
            ("batata frita", "french fries"),
            ("água de coco", "coconut water"),
            ("feijão preto", "black beans"),
            ("arroz integral", "brown rice"),
            ("pimenta do reino", "black pepper"),
            ("limão siciliano", "lemon"),
        ):
            with self.subTest(pt=pt, en=en):
                self.assertTrue(synonyms.same_product(pt, en))

    def test_a_trailing_connector_lookalike_is_not_stripped(self):
        # "castanha do pará" folds to "... do para", and "para" is also the
        # preposition. Stripping it would leave bare "castanha" and turn the
        # Brazil nut group into a trap that swallows every other nut.
        self.assertEqual(synonyms.canonical("castanha do pará"), "brazil nut")
        self.assertEqual(synonyms.canonical("castanha"), "castanha")
        self.assertIsNone(synonyms.group_for("castanha"))
        self.assertFalse(synonyms.same_product("castanha", "castanha do pará"))
        self.assertFalse(synonyms.same_product("castanha de caju", "castanha do pará"))

    def test_connectors_are_optional_inside_a_phrase(self):
        self.assertEqual(synonyms.canonical("leite coco"), synonyms.canonical("leite de coco"))
        self.assertEqual(synonyms.canonical("agua coco"), synonyms.canonical("água de coco"))
        self.assertEqual(
            synonyms.canonical("pasta dente"), synonyms.canonical("pasta de dente")
        )

    def test_unlisted_qualified_names_stay_separate_rather_than_guessing(self):
        # Under-merging is the deliberate failure mode: an unknown qualifier
        # passes through instead of being folded into its head noun.
        for name, head in (
            ("leite de vaca", "leite"),
            ("pão de queijo", "pão"),
            ("queijo minas", "queijo"),
            ("arroz japonês", "arroz"),
        ):
            with self.subTest(name=name):
                self.assertNotEqual(synonyms.canonical(name), synonyms.canonical(head))
                self.assertEqual(synonyms.canonical(name), synonyms.fold(name))


class UnknownProductTests(unittest.TestCase):
    def test_unknown_names_pass_through_folded(self):
        for name in ("goiabada cascão", "Rapadura", "kombucha de gengibre", "farofa pronta"):
            with self.subTest(name=name):
                self.assertEqual(synonyms.canonical(name), synonyms.fold(name))

    def test_canonical_never_returns_none(self):
        for name in ("", "   ", "???", "•", "leite", "unknown thing", "123", "🍌"):
            with self.subTest(name=name):
                self.assertIsInstance(synonyms.canonical(name), str)

    def test_a_name_that_folds_away_still_yields_a_key(self):
        self.assertEqual(synonyms.canonical("???"), "???")
        self.assertEqual(synonyms.canonical(""), "")

    def test_canonical_is_idempotent(self):
        for name in (
            "leite", "MILK", "leite de coco", "creme de leite", "2 kg de arroz",
            "goiabada cascão", "papel higiênico", "???", "",
        ):
            with self.subTest(name=name):
                once = synonyms.canonical(name)
                self.assertEqual(synonyms.canonical(once), once)

    def test_every_canonical_key_is_its_own_fixed_point(self):
        for key in synonyms.CANONICAL_KEYS:
            with self.subTest(key=key):
                self.assertEqual(synonyms.canonical(key), key)


class SameProductTests(unittest.TestCase):
    def test_true_for_known_pairs(self):
        self.assertTrue(synonyms.same_product("Leite", "MILK"))
        self.assertTrue(synonyms.same_product("mandioca", "macaxeira"))

    def test_false_for_different_products(self):
        self.assertFalse(synonyms.same_product("leite", "queijo"))
        self.assertFalse(synonyms.same_product("rapadura", "paçoca"))

    def test_identical_unknown_names_still_match(self):
        self.assertTrue(synonyms.same_product("goiabada cascão", "Goiabada Cascao"))

    def test_empty_names_match_nothing(self):
        self.assertFalse(synonyms.same_product("", ""))
        self.assertFalse(synonyms.same_product("", "leite"))
        self.assertFalse(synonyms.same_product("   ", "milk"))


class GroupForTests(unittest.TestCase):
    def test_returns_the_other_spellings(self):
        group = synonyms.group_for("milk")
        self.assertIsNotNone(group)
        self.assertIn("leite", group)
        self.assertIn("milk", group)

    def test_works_from_either_language_and_from_a_plural(self):
        self.assertEqual(synonyms.group_for("leite"), synonyms.group_for("milk"))
        self.assertEqual(synonyms.group_for("ovos"), synonyms.group_for("egg"))
        self.assertEqual(synonyms.group_for("MAÇÃS"), synonyms.group_for("apple"))

    def test_regional_group_lists_every_variant(self):
        group = synonyms.group_for("aipim")
        self.assertIsNotNone(group)
        for term in ("mandioca", "aipim", "macaxeira", "cassava"):
            self.assertIn(term, group)

    def test_none_for_an_unknown_product(self):
        self.assertIsNone(synonyms.group_for("goiabada cascão"))
        self.assertIsNone(synonyms.group_for("creme de leite"))
        self.assertIsNone(synonyms.group_for(""))

    def test_group_matches_the_canonical_key(self):
        for name in ("leite", "milk", "bergamota", "papel higiênico"):
            with self.subTest(name=name):
                self.assertEqual(synonyms.group_for(name), synonyms.GROUPS[synonyms.canonical(name)])


class DeliberateOmissionTests(unittest.TestCase):
    """Terms left ungrouped on purpose must stay ungrouped."""

    AMBIGUOUS = ("salsa", "detergent", "soap", "fermento", "vitamina", "pimenta",
                 "pepper", "creme", "massa", "torta", "caldo", "kale")

    def test_bare_pasta_is_not_the_food(self):
        """pt-BR 'pasta' is a folder; over-merging would replace a real item."""
        self.assertNotEqual(synonyms.canonical("pasta"), synonyms.canonical("macarrão"))
        # The qualified forms still resolve correctly.
        self.assertEqual(synonyms.canonical("pasta de dente"), "toothpaste")
        self.assertEqual(synonyms.canonical("macarrão"), synonyms.canonical("noodles"))

    def test_ambiguous_terms_are_not_in_any_group(self):
        for term in self.AMBIGUOUS:
            with self.subTest(term=term):
                self.assertIsNone(synonyms.group_for(term))
                self.assertEqual(synonyms.canonical(term), synonyms.fold(term))

    def test_meat_and_beef_are_kept_apart(self):
        self.assertFalse(synonyms.same_product("carne", "carne bovina"))
        self.assertTrue(synonyms.same_product("carne", "meat"))
        self.assertTrue(synonyms.same_product("carne bovina", "beef"))


if __name__ == "__main__":
    unittest.main()

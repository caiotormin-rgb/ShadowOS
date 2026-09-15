import tempfile
import unittest
from pathlib import Path

import groups
import grocery
import guide
import people


class GuideTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = grocery.connect(Path(self.temp.name) / "t.sqlite3")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_onboarding_greets_by_name_in_their_language(self):
        groups.add_member(self.conn, None, "+5511900000021", "member")
        people.remember_person(self.conn, "+5511900000021", "pt", "Kim")
        text = guide.onboarding(self.conn, "+5511900000021")
        self.assertIn("Oi, Kim!", text)
        self.assertIn("adiciona leite", text)

    def test_onboarding_works_for_someone_with_no_name(self):
        text = guide.onboarding(self.conn, None, "en")
        self.assertTrue(text.startswith("Hi!"))
        self.assertIn("add milk", text)

    def test_onboarding_says_the_list_is_shared(self):
        """People should know their additions are visible before they add."""
        for lang, phrase in (("en", "shared with the household"),
                             ("pt", "compartilhada com a casa")):
            self.assertIn(phrase, guide.onboarding(self.conn, None, lang))

    def test_onboarding_names_known_stores_only_when_there_are_some(self):
        group = groups.group_row(self.conn, None, create=True)
        self.assertNotIn("Stores on file", guide.onboarding(self.conn, None, "en", group["id"]))
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Milk"]'), "text",
            group_id=group["id"],
        )
        self.assertIn("Costco", guide.onboarding(self.conn, None, "en", group["id"]))

    def test_onboarding_asks_rather_than_only_telling(self):
        """It is the one moment where asking beats assuming: once, off the aisle."""
        for lang, question in (("en", "Which store do you usually mean"),
                               ("pt", "Qual loja você normalmente quer dizer")):
            text = guide.onboarding(self.conn, None, lang)
            self.assertIn(question, text)
            self.assertIn("?", text)

    def test_onboarding_offers_an_easy_out(self):
        self.assertIn("Answer however you like", guide.onboarding(self.conn, None, "en"))
        self.assertIn("do jeito que quiser", guide.onboarding(self.conn, None, "pt"))

    def test_help_shows_phrases_not_subcommands(self):
        """Nobody in a supermarket types a subcommand."""
        for lang in ("en", "pt"):
            text = guide.help_text(self.conn, None, lang)
            self.assertNotIn("--store", text)
            self.assertNotIn("grocery.py", text)
            self.assertIn('"', text)

    def test_help_mentions_asking_what_changed(self):
        """The activity feed is only useful if people know they can ask."""
        self.assertIn("who bought the milk?", guide.help_text(self.conn, None, "en"))
        self.assertIn("o que mudou na lista hoje?", guide.help_text(self.conn, None, "pt"))

    def test_help_states_the_one_thing_it_confirms(self):
        self.assertIn("Removing something", guide.help_text(self.conn, None, "en"))
        self.assertIn("Remover", guide.help_text(self.conn, None, "pt"))

    def test_text_is_messaging_safe(self):
        for render in (guide.onboarding, guide.help_text):
            for lang in ("en", "pt"):
                text = render(self.conn, None, lang)
                self.assertNotIn("|", text)          # no tables
                self.assertNotIn("#", text)          # no headers

    def test_language_follows_the_person(self):
        people.remember_person(self.conn, "sam", "en", "Sam")
        people.remember_person(self.conn, "kim", "pt", "Kim")
        self.assertIn("Hi, Sam!", guide.onboarding(self.conn, "sam"))
        self.assertIn("Oi, Kim!", guide.onboarding(self.conn, "kim"))


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

import groups
import grocery
import people
import roster


ROSTER = {
    "version": 1,
    "group": "Household",
    "members": [
        {"name": "Jo", "phone": "+12025550101", "role": "owner", "lang": "pt"},
        {"name": "Kim", "phone": "+55 11 90000-0021", "role": "member", "lang": "pt"},
        {"name": "Sam", "phone": "+15551234567", "role": "member", "lang": "en"},
    ],
}


class RosterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.conn = grocery.connect(self.dir / "t.sqlite3")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def write(self, data):
        path = self.dir / "members.json"
        path.write_text(json.dumps(data))
        return path

    def test_numbers_are_normalized_to_one_identity(self):
        """+55 11 90000-0021 and +5511900000021 are one person, not two."""
        path = self.write(ROSTER)
        loaded = roster.load(path)
        self.assertEqual(loaded["members"][1]["phone"], "+5511900000021")

    def test_apply_enrolls_everyone_with_role_and_language(self):
        roster.apply(self.conn, self.write(ROSTER))
        listed = {m["actor"]: m for m in groups.members(self.conn, "Household")}
        self.assertEqual(len(listed), 3)
        self.assertEqual(listed["+12025550101"]["role"], "owner")
        self.assertEqual(listed["+5511900000021"]["name"], "Kim")
        self.assertEqual(people.language_for(self.conn, "+15551234567"), "en")
        self.assertEqual(people.language_for(self.conn, "+5511900000021"), "pt")

    def test_apply_is_idempotent(self):
        path = self.write(ROSTER)
        roster.apply(self.conn, path)
        roster.apply(self.conn, path)
        self.assertEqual(len(groups.members(self.conn, "Household")), 3)

    def test_editing_a_role_or_language_takes_effect(self):
        path = self.write(ROSTER)
        roster.apply(self.conn, path)
        changed = json.loads(json.dumps(ROSTER))
        changed["members"][2]["role"] = "owner"
        changed["members"][2]["lang"] = "pt"
        roster.apply(self.conn, self.write(changed))
        listed = {m["actor"]: m for m in groups.members(self.conn, "Household")}
        self.assertEqual(listed["+15551234567"]["role"], "owner")
        self.assertEqual(people.language_for(self.conn, "+15551234567"), "pt")

    def test_removal_is_reported_but_not_applied_without_prune(self):
        path = self.write(ROSTER)
        roster.apply(self.conn, path)
        fewer = json.loads(json.dumps(ROSTER))
        fewer["members"] = fewer["members"][:2]

        result = roster.apply(self.conn, self.write(fewer))
        self.assertEqual(result["not_in_file"], ["+15551234567"])
        self.assertEqual(result["revoked"], [])
        # Still enrolled: revocation is the one step that needs saying so.
        self.assertEqual(
            groups.group_for_actor(self.conn, "+15551234567")["name"], "Household"
        )

    def test_prune_revokes_access(self):
        path = self.write(ROSTER)
        roster.apply(self.conn, path)
        fewer = json.loads(json.dumps(ROSTER))
        fewer["members"] = fewer["members"][:2]
        result = roster.apply(self.conn, self.write(fewer), prune=True)
        self.assertEqual(result["revoked"], ["+15551234567"])
        with self.assertRaises(grocery.GroceryError):
            groups.group_for_actor(self.conn, "+15551234567")

    def test_a_duplicate_number_is_refused(self):
        dupe = json.loads(json.dumps(ROSTER))
        dupe["members"].append({"name": "Typo", "phone": "+12025550101", "role": "member"})
        with self.assertRaises(grocery.GroceryError):
            roster.load(self.write(dupe))

    def test_bad_input_is_refused_clearly(self):
        for broken, why in [
            ({"members": []}, "empty"),
            ({"members": [{"name": "X", "phone": "5511900000021"}]}, "no country code"),
            ({"members": [{"name": "X", "phone": "+1555", "role": "admin"}]}, "bad role"),
            ({"members": [{"name": "X", "phone": "+15551234567", "lang": "es"}]}, "bad lang"),
        ]:
            with self.subTest(why=why):
                with self.assertRaises(grocery.GroceryError):
                    roster.load(self.write(broken))

    def test_missing_file_says_so(self):
        with self.assertRaises(grocery.GroceryError):
            roster.load(self.dir / "nope.json")

    def test_the_shipped_example_is_valid(self):
        example = Path(__file__).resolve().parent.parent / "config" / "members.example.json"
        loaded = roster.load(example)
        self.assertTrue(loaded["members"])


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

import groups
import grocery


class GroupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = grocery.connect(Path(self.temp.name) / "t.sqlite3")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_gate_opens_until_configured_then_closes(self):
        """An empty deployment must not lock its owner out of their own list."""
        self.assertEqual(groups.group_for_actor(self.conn, None)["name"], "Household")
        self.assertEqual(groups.group_for_actor(self.conn, "anyone")["name"], "Household")

        groups.add_member(self.conn, None, "owner", "owner")
        self.assertEqual(groups.group_for_actor(self.conn, "owner")["name"], "Household")
        with self.assertRaises(grocery.GroceryError):
            groups.group_for_actor(self.conn, "+15559999")
        with self.assertRaises(grocery.GroceryError):
            groups.group_for_actor(self.conn, None)

    def test_two_households_can_both_have_a_costco(self):
        a = groups.group_row(self.conn, "Example", create=True)
        b = groups.group_row(self.conn, "Neighbours", create=True)
        groups.add_member(self.conn, "Example", "owner", "owner")
        groups.add_member(self.conn, "Neighbours", "sam", "owner")

        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Milk"]'), "text",
            actor="owner", group_id=a["id"],
        )
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Beer"]'), "text",
            actor="sam", group_id=b["id"],
        )

        _, mine = grocery.current_items(self.conn, "Costco", True, a["id"])
        _, theirs = grocery.current_items(self.conn, "Costco", True, b["id"])
        self.assertEqual([r["name"] for r in mine], ["Milk"])
        self.assertEqual([r["name"] for r in theirs], ["Beer"])

    def test_a_member_cannot_reach_another_household(self):
        a = groups.group_row(self.conn, "Example", create=True)
        groups.add_member(self.conn, "Example", "owner", "owner")
        groups.add_member(self.conn, "Neighbours", "sam", "owner")
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Milk"]'), "text",
            group_id=a["id"],
        )
        # Sam resolves to his own group, where that store does not exist.
        sam = groups.group_for_actor(self.conn, "sam")["id"]
        with self.assertRaises(grocery.GroceryError):
            grocery.current_items(self.conn, "Costco", True, sam)

    def test_store_fallback_does_not_leak_across_households(self):
        a = groups.group_row(self.conn, "Example", create=True)
        b = groups.group_row(self.conn, "Neighbours", create=True)
        grocery.ingest_items(
            self.conn, "Wegmans", grocery.parse_items_json('["Milk"]'), "text",
            group_id=a["id"],
        )
        # The other household has activity but none of its own; falling back to
        # the neighbour's store would be a disclosure, not a convenience.
        with self.assertRaises(grocery.GroceryError):
            grocery.resolve_store(self.conn, None, b["id"])
        name, _ = grocery.resolve_store(self.conn, None, a["id"])
        self.assertEqual(name, "Wegmans")

    def test_store_and_event_listings_do_not_span_households(self):
        """Listing must not disclose that another household exists."""
        import cli
        a = groups.group_row(self.conn, "Example", create=True)
        b = groups.group_row(self.conn, "Neighbours", create=True)
        groups.add_member(self.conn, "Example", "owner", "owner")
        groups.add_member(self.conn, "Neighbours", "sam", "owner")
        grocery.ingest_items(
            self.conn, "Wegmans", grocery.parse_items_json('["Milk"]'), "text",
            actor="owner", group_id=a["id"],
        )
        grocery.ingest_items(
            self.conn, "Aldi", grocery.parse_items_json('["Beer"]'), "text",
            actor="sam", group_id=b["id"],
        )

        mine = [r["name"] for r in self.conn.execute(
            "SELECT name FROM stores WHERE group_id = ?", (a["id"],))]
        theirs = [r["name"] for r in self.conn.execute(
            "SELECT name FROM stores WHERE group_id = ?", (b["id"],))]
        self.assertEqual(mine, ["Wegmans"])
        self.assertEqual(theirs, ["Aldi"])

        scoped = [r["store"] for r in self.conn.execute(
            "SELECT store FROM events WHERE store IN "
            "(SELECT name FROM stores WHERE group_id = ?)", (a["id"],))]
        self.assertEqual(set(scoped), {"Wegmans"})

    def test_membership_is_updated_not_duplicated(self):
        groups.add_member(self.conn, None, "owner", "member")
        groups.add_member(self.conn, None, "owner", "owner")
        listed = groups.members(self.conn, None)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["role"], "owner")

    def test_removing_a_member_revokes_access(self):
        groups.add_member(self.conn, None, "owner", "owner")
        groups.add_member(self.conn, None, "sam", "member")
        groups.remove_member(self.conn, None, "sam")
        with self.assertRaises(grocery.GroceryError):
            groups.group_for_actor(self.conn, "sam")
        with self.assertRaises(grocery.GroceryError):
            groups.remove_member(self.conn, None, "sam")

    def test_members_carry_their_name_and_language(self):
        import people
        groups.add_member(self.conn, None, "+15551234567", "owner")
        people.remember_person(self.conn, "+15551234567", "pt", "Jo")
        listed = groups.members(self.conn, None)
        self.assertEqual(listed[0]["name"], "Jo")
        self.assertEqual(listed[0]["lang"], "pt")

    def test_history_does_not_span_households_sharing_a_store_name(self):
        """Two households may each have a Costco; their pasts stay separate."""
        import insights
        a = groups.group_row(self.conn, "Example", create=True)
        b = groups.group_row(self.conn, "Neighbours", create=True)
        for group in (a, b):
            grocery.ingest_items(
                self.conn, "Costco", grocery.parse_items_json('["Caviar"]'),
                "text", group_id=group["id"],
            )
        grocery.set_status(self.conn, "Costco", ["Caviar"], "purchased",
                           group_id=a["id"])
        grocery.close_trip(self.conn, "Costco", group_id=a["id"])

        bought = insights.purchase_history(self.conn, "Costco", group_id=a["id"])
        self.assertEqual([r["name"] for r in bought], ["Caviar"])
        self.assertEqual(
            insights.purchase_history(self.conn, "Costco", group_id=b["id"]), []
        )
        self.assertEqual(insights.on_list(self.conn, "Costco", b["id"]), {"caviar"})
        rows = self.conn.execute(
            "SELECT group_id FROM events WHERE item_name = 'Caviar'"
        ).fetchall()
        self.assertEqual({r["group_id"] for r in rows}, {a["id"], b["id"]})


    def _two_households(self):
        """Group 1 populated, a second household added after — the live shape.

        Setting both households up as fresh groups hides these bugs: the
        default-group fallback then lands on an empty group and raises
        "unknown store" instead of quietly serving the wrong household.
        """
        a = groups.group_row(self.conn, None, create=True)
        groups.add_member(self.conn, None, "owner", "owner")
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Caviar"]'),
            "text", actor="owner", group_id=a["id"],
        )
        b = groups.group_row(self.conn, "Neighbours", create=True)
        groups.add_member(self.conn, "Neighbours", "sam", "owner")
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Beans"]'),
            "text", actor="sam", group_id=b["id"],
        )
        return a, b

    def test_share_renders_the_callers_household_not_the_first_one(self):
        """The one bug here that sends another family's data off the machine."""
        a, b = self._two_households()
        grocery.add_contact(self.conn, "Pal", "whatsapp", "+15550001111")
        payload = grocery.prepare_share(self.conn, "Costco", "Pal", b["id"])["payload"]
        self.assertIn("Beans", payload)
        self.assertNotIn("Caviar", payload)
        self.assertIn("Caviar", grocery.prepare_share(
            self.conn, "Costco", "Pal", a["id"])["payload"])

    def test_delivery_cannot_be_marked_on_another_households_share(self):
        a, b = self._two_households()
        grocery.add_contact(self.conn, "Pal", "whatsapp", "+15550001111")
        share = grocery.prepare_share(self.conn, "Costco", "Pal", a["id"])
        with self.assertRaises(grocery.GroceryError):
            grocery.mark_delivered(self.conn, share["share_id"], b["id"])
        self.assertTrue(
            grocery.mark_delivered(self.conn, share["share_id"], a["id"])["delivered_at"]
        )

    def test_footer_never_names_another_households_member(self):
        """The actor is a phone number, so this discloses an identifier."""
        import render
        a, b = self._two_households()
        # Jo acts last, at his own Costco. Sam's list must not mention him.
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Champagne"]'),
            "text", actor="owner", group_id=a["id"],
        )
        self.assertEqual(
            render.last_touched(self.conn, "Costco", b["id"])["actor"], "sam")
        self.assertEqual(
            render.last_touched(self.conn, "Costco", a["id"])["actor"], "owner")

    def test_a_retried_close_is_still_suppressed_across_households(self):
        """Another household's activity must not reset the duplicate guard.

        The guard asks whether anything happened at this store since the last
        close. Counting the neighbours' events answers yes, so the retry runs
        and writes a trip that never happened into real history.
        """
        a, b = self._two_households()
        # Something unbought has to survive the close, or the retry trips over
        # an empty list and never reaches the guard this test is about.
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Champagne"]'),
            "text", actor="owner", group_id=a["id"],
        )
        grocery.set_status(self.conn, "Costco", ["Caviar"], "purchased",
                           actor="owner", group_id=a["id"])
        first = grocery.close_trip(self.conn, "Costco", actor="owner", group_id=a["id"])
        self.assertFalse(first["duplicate"])
        # The neighbours shop while the retry is in flight.
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Rice"]'),
            "text", actor="sam", group_id=b["id"],
        )
        retry = grocery.close_trip(self.conn, "Costco", actor="owner", group_id=a["id"])
        self.assertTrue(retry["duplicate"])
        self.assertEqual(retry["trip_id"], first["trip_id"])
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM trips WHERE store_id IN "
                "(SELECT id FROM stores WHERE group_id = ?)", (a["id"],)
            ).fetchone()[0],
            1,
        )


    def test_rejects_an_invented_role(self):
        with self.assertRaises(grocery.GroceryError):
            groups.add_member(self.conn, None, "owner", "admin")


if __name__ == "__main__":
    unittest.main()

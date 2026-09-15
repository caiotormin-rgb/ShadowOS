"""Sharing is a state plus counts. It is never a list of people."""
import json
import unittest

from drivectx.sharing import STATES, Sharing, summarize
from drivectx.store import Store, migrate
from drivectx.sync import parse_file
from tests.fixtures import memory_conn, perm, raw_file


class SharingDerivationTest(unittest.TestCase):
    def state(self, permissions):
        return summarize({"permissions": permissions}).state

    # -- the four derivable states ---------------------------------------
    def test_owner_only_is_private(self):
        self.assertEqual(self.state([perm("user", "owner")]), "private")

    def test_a_named_user_beyond_the_owner_is_shared_with_named(self):
        self.assertEqual(
            self.state([perm("user", "owner"), perm("user", "reader")]), "shared_with_named")

    def test_a_group_grant_is_shared_with_named(self):
        self.assertEqual(self.state([perm("user", "owner"), perm("group")]), "shared_with_named")

    def test_a_domain_grant_beats_a_named_grant(self):
        self.assertEqual(
            self.state([perm("user", "reader"), perm("domain")]), "domain")

    def test_anyone_beats_everything(self):
        self.assertEqual(
            self.state([perm("user", "owner"), perm("domain"), perm("anyone")]),
            "anyone_with_link")

    # -- the fifth state, which is the one that matters ------------------
    def test_absent_permissions_are_unknown_not_private(self):
        """Under drive.metadata.readonly the permissions collection is invisible
        for files shared *to* this account. Calling that 'private' would be a
        lie in the most dangerous direction."""
        s = summarize({"id": "f", "name": "x"})
        self.assertEqual(s.state, "unknown")
        self.assertEqual((s.named_user_count, s.group_count, s.max_role), (0, 0, None))

    def test_an_empty_permission_list_is_also_unknown(self):
        """A file always has at least its owner's permission, so seeing zero is
        a statement about visibility, not about sharing."""
        self.assertEqual(self.state([]), "unknown")

    def test_unknown_is_a_declared_state(self):
        self.assertIn("unknown", STATES)

    # -- counts ------------------------------------------------------------
    def test_counts_exclude_the_owner_and_separate_groups(self):
        s = summarize({"permissions": [
            perm("user", "owner"), perm("user", "writer"), perm("user", "reader"),
            perm("group", "reader"), perm("domain", "reader")]})
        self.assertEqual(s.named_user_count, 2, "the owner is not a grantee")
        self.assertEqual(s.group_count, 1)

    def test_max_role_is_the_broadest_granted(self):
        self.assertEqual(summarize({"permissions": [
            perm("user", "reader"), perm("user", "writer"),
            perm("user", "commenter")]}).max_role, "writer")
        self.assertEqual(summarize({"permissions": [
            perm("user", "owner"), perm("user", "writer")]}).max_role, "owner")

    def test_an_unrecognised_role_is_ignored_rather_than_guessed(self):
        """'organizer' only exists on shared drives, which are excluded."""
        s = summarize({"permissions": [perm("user", "organizer")]})
        self.assertIsNone(s.max_role)

    def test_link_discoverability_is_recorded(self):
        self.assertTrue(summarize({"permissions": [
            perm("anyone", discoverable=True)]}).link_discoverable)
        self.assertFalse(summarize({"permissions": [
            perm("anyone", discoverable=False)]}).link_discoverable)

    def test_grants_to_deleted_accounts_are_ignored(self):
        s = summarize({"permissions": [perm("user", "owner"),
                                       perm("user", "writer", deleted=True)]})
        self.assertEqual((s.state, s.named_user_count), ("private", 0))

    def test_malformed_permission_entries_do_not_crash_the_parse(self):
        s = summarize({"permissions": ["nonsense", None, {"type": "user", "role": "reader"}]})
        self.assertEqual(s.state, "shared_with_named")

    def test_permissions_of_the_wrong_shape_are_unknown(self):
        self.assertEqual(summarize({"permissions": "yes"}).state, "unknown")


class NoGranteeLeakTest(unittest.TestCase):
    """Even handed a payload Drive would never send, no address may survive."""

    def setUp(self):
        self.conn = memory_conn()
        migrate(self.conn)
        self.store = Store(self.conn)

    def test_a_hostile_payload_carrying_grantee_addresses_stores_none_of_them(self):
        raw = raw_file("f1", permissions=[
            {"type": "user", "role": "owner", "emailAddress": "caio@example.invalid"},
            {"type": "user", "role": "writer", "emailAddress": "GRANTEE@example.invalid",
             "displayName": "Grantee Person"},
            {"type": "group", "role": "reader", "emailAddress": "TEAM@example.invalid"},
        ])
        parsed = parse_file(raw)
        self.assertNotIn("GRANTEE", repr(parsed))
        self.store.upsert_file(parsed)

        dump = json.dumps([dict(r) for r in self.conn.execute(
            "SELECT * FROM drive_files")] + [dict(r) for r in self.conn.execute(
            "SELECT * FROM drive_owners")])
        self.assertNotIn("GRANTEE", dump)
        self.assertNotIn("TEAM", dump)
        self.assertIn("caio@example.invalid", dump, "the owner is retained on purpose")

    def test_the_summary_still_counts_what_it_refuses_to_name(self):
        parsed = parse_file(raw_file("f1", permissions=[
            {"type": "user", "role": "owner", "emailAddress": "caio@example.invalid"},
            {"type": "user", "role": "writer", "emailAddress": "a@example.invalid"},
            {"type": "user", "role": "reader", "emailAddress": "b@example.invalid"},
        ]))
        self.assertEqual(parsed.sharing, Sharing(
            state="shared_with_named", named_user_count=2, group_count=0,
            link_discoverable=False, max_role="owner"))


if __name__ == "__main__":
    unittest.main()

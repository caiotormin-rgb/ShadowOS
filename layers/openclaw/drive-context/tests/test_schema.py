import sqlite3
import unittest

from drivectx.store import migrate
from tests.fixtures import memory_conn

# Words that would indicate someone started storing content, an exported
# representation, a content identifier, or a capability-bearing URL.
FORBIDDEN_COLUMN_TOKENS = (
    "content", "body", "payload", "blob", "raw", "text_plain", "thumbnail",
    "icon", "checksum", "md5", "sha1", "sha256", "export", "download", "media",
    "resource_key",
)

# The one place an address is allowed to live: one identity per file, not an
# unbounded set of third parties. See README.md for why owner is retained and
# grantee is not.
ALLOWED_EMAIL_COLUMNS = {("drive_owners", "email_address")}


class SchemaTest(unittest.TestCase):
    def setUp(self):
        self.conn = memory_conn()
        migrate(self.conn)

    def tables(self):
        return [r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")
            if not r[0].startswith("drive_search")]

    def columns(self):
        for table in self.tables():
            for col in self.conn.execute(f"PRAGMA table_info({table})"):
                yield table, col[1]

    def test_migrate_is_idempotent(self):
        migrate(self.conn)
        migrate(self.conn)
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_refuses_newer_schema(self):
        self.conn.execute("PRAGMA user_version = 99")
        with self.assertRaises(RuntimeError):
            migrate(self.conn)

    def test_no_content_or_checksum_columns(self):
        """The no-content rule is enforced against the live schema, not the docs."""
        offenders = []
        for table, name in self.columns():
            low = name.lower()
            for tok in FORBIDDEN_COLUMN_TOKENS:
                if tok in low:
                    offenders.append(f"{table}.{name}")
        self.assertEqual(offenders, [], f"content-like columns found: {offenders}")

    def test_no_column_is_declared_as_a_blob(self):
        """Names can be innocent while the type is not. The only quantity of
        bytes this schema holds is `size_bytes`, which is a count Drive reported,
        not a payload -- and it is declared INTEGER, which is the point."""
        offenders = []
        for table in self.tables():
            for col in self.conn.execute(f"PRAGMA table_info({table})"):
                if "blob" in (col[2] or "").lower():
                    offenders.append(f"{table}.{col[1]} {col[2]}")
        self.assertEqual(offenders, [], f"blob columns found: {offenders}")
        self.assertEqual(
            [c[2] for c in self.conn.execute("PRAGMA table_info(drive_files)")
             if c[1] == "size_bytes"], ["INTEGER"])

    def test_the_only_address_column_is_the_owner(self):
        """No grantee email may reach the index, so no column may hold one."""
        offenders = [(t, c) for t, c in self.columns()
                     if ("email" in c.lower() or "addr" in c.lower())
                     and (t, c) not in ALLOWED_EMAIL_COLUMNS]
        self.assertEqual(offenders, [], f"unexpected address columns: {offenders}")

    def test_there_is_no_permissions_table(self):
        self.assertNotIn("drive_permissions", self.tables())

    def test_sharing_state_is_constrained(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO drive_files (file_id, sharing_state, synced_at) VALUES ('f','public',0)")

    def test_unknown_is_a_valid_sharing_state(self):
        """'unknown' is a real state, not an error, and must be storable."""
        self.conn.execute(
            "INSERT INTO drive_files (file_id, sharing_state, synced_at) VALUES ('f','unknown',0)")
        self.assertEqual(self.conn.execute(
            "SELECT sharing_state FROM drive_files").fetchone()[0], "unknown")

    def test_max_role_is_constrained(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO drive_files (file_id, max_role, synced_at) VALUES ('f','organizer',0)")

    def test_kind_is_constrained(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO drive_files (file_id, kind, synced_at) VALUES ('f','symlink',0)")

    def test_run_kind_is_constrained(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO drive_sync_runs (kind, started_at) VALUES ('download', 0)")

    def test_parent_edges_are_unique_per_pair(self):
        self.conn.execute("INSERT INTO drive_files (file_id, synced_at) VALUES ('f',0)")
        self.conn.execute("INSERT INTO drive_parents (file_id, parent_id) VALUES ('f','p')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO drive_parents (file_id, parent_id) VALUES ('f','p')")

    def test_a_parent_edge_may_point_at_a_file_that_is_not_indexed(self):
        """The edge routinely outlives the node: a parent can be a folder this
        account cannot see. A foreign key here would reject real data."""
        self.conn.execute("INSERT INTO drive_files (file_id, synced_at) VALUES ('f',0)")
        self.conn.execute("INSERT INTO drive_parents (file_id, parent_id) VALUES ('f','invisible')")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM drive_parents").fetchone()[0], 1)

    def test_deleting_a_file_cascades_to_edges_and_owners(self):
        self.conn.execute("INSERT INTO drive_files (file_id, synced_at) VALUES ('f',0)")
        self.conn.execute("INSERT INTO drive_parents (file_id, parent_id) VALUES ('f','p')")
        self.conn.execute(
            "INSERT INTO drive_owners (file_id, display_name, email_address) VALUES ('f','N','e')")
        self.conn.execute("DELETE FROM drive_files WHERE file_id='f'")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM drive_parents").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM drive_owners").fetchone()[0], 0)

    def test_fts_index_follows_inserts_updates_and_deletes(self):
        self.conn.execute(
            "INSERT INTO drive_files (file_id, name, synced_at) VALUES ('f','quarterly',0)")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM drive_search WHERE drive_search MATCH 'quarterly'").fetchone()[0], 1)
        self.conn.execute("UPDATE drive_files SET name='annual' WHERE file_id='f'")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM drive_search WHERE drive_search MATCH 'quarterly'").fetchone()[0], 0)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM drive_search WHERE drive_search MATCH 'annual'").fetchone()[0], 1)
        self.conn.execute("DELETE FROM drive_files WHERE file_id='f'")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM drive_search WHERE drive_search MATCH 'annual'").fetchone()[0], 0)

    def test_sync_state_is_a_singleton(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO drive_sync_state (id) VALUES (2)")


if __name__ == "__main__":
    unittest.main()

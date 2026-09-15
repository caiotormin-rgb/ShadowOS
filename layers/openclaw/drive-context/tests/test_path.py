"""Path resolution against a deliberately hostile parent graph.

Drive's parents are a DAG in principle and an untrusted graph in practice. Every
one of these cases is real: multi-parent files exist in legacy data, orphans are
reachable by id, a parent can name a folder this account cannot see, and nothing
in the API promises the absence of a cycle.
"""
import tempfile
import unittest
from pathlib import Path

from drivectx.drive import FOLDER_MIME
from drivectx.query import MAX_PATH_DEPTH, MAX_PATHS, DriveContext
from drivectx.store import Store, connect, migrate
from tests.fixtures import dfile


class PathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "drive.sqlite"
        self.conn = connect(self.db)
        migrate(self.conn)
        self.store = Store(self.conn)
        self.store.set_root_folder_id("ROOT")

    def folder(self, file_id, name, parents=()):
        self.store.upsert_file(dfile(file_id, name=name, mime=FOLDER_MIME, parents=parents))

    def ctx(self):
        c = DriveContext(self.db, waiver_path=Path(self.tmp.name) / "none")
        self.addCleanup(c.close)
        return c

    def paths(self, file_id):
        return self.ctx().path(file_id)

    # -- the ordinary case -------------------------------------------------
    def test_a_normal_file_resolves_top_down_to_my_drive(self):
        self.folder("ROOT", "My Drive")
        self.folder("d1", "Work", parents=("ROOT",))
        self.folder("d2", "Taxes", parents=("d1",))
        self.store.upsert_file(dfile("f1", name="2025.pdf", parents=("d2",)))
        res = self.paths("f1")
        self.assertEqual(len(res.rows), 1)
        row = res.rows[0]
        self.assertEqual(row["root"], "my_drive")
        self.assertTrue(row["complete"])
        self.assertEqual(row["display"], "/My Drive/Work/Taxes/2025.pdf")
        self.assertEqual([s["file_id"] for s in row["segments"]], ["ROOT", "d1", "d2", "f1"])

    def test_the_root_is_recognised_even_when_it_is_not_indexed(self):
        """files.list does not always return the root folder itself."""
        self.folder("d1", "Work", parents=("ROOT",))
        self.store.upsert_file(dfile("f1", name="notes", parents=("d1",)))
        row = self.paths("f1").rows[0]
        self.assertEqual(row["root"], "my_drive")
        self.assertEqual(row["display"], "/Work/notes")

    # -- the hostile cases --------------------------------------------------
    def test_a_multi_parent_file_returns_every_path_it_has(self):
        """Collapsing them to the first would be a lie about the data."""
        self.folder("a", "Alpha", parents=("ROOT",))
        self.folder("b", "Beta", parents=("ROOT",))
        self.store.upsert_file(dfile("f1", name="shared.txt", parents=("a", "b")))
        rows = self.paths("f1").rows
        self.assertEqual(len(rows), 2)
        self.assertEqual(sorted(r["display"] for r in rows),
                         ["/Alpha/shared.txt", "/Beta/shared.txt"])

    def test_a_file_with_no_parents_is_reported_as_an_orphan(self):
        self.store.upsert_file(dfile("f1", name="loose"))
        row = self.paths("f1").rows[0]
        self.assertEqual(row["root"], "orphan")
        self.assertTrue(row["complete"], "orphaned is an answer, not a failure")
        self.assertEqual(row["display"], "(orphan)/loose")

    def test_a_parent_that_is_not_in_the_index_ends_the_path_explicitly(self):
        """The edge outlives the node when the parent folder is invisible to
        this account. Guessing or dropping the branch would both be wrong."""
        self.store.upsert_file(dfile("f1", name="notes", parents=("invisible",)))
        row = self.paths("f1").rows[0]
        self.assertEqual(row["root"], "unresolved_parent")
        self.assertFalse(row["complete"])
        self.assertEqual(row["segments"][0]["file_id"], "invisible")
        self.assertTrue(row["segments"][0]["unresolved"])

    def test_a_parent_cycle_terminates_instead_of_looping(self):
        self.folder("a", "Alpha", parents=("b",))
        self.folder("b", "Beta", parents=("a",))
        self.store.upsert_file(dfile("f1", name="stuck", parents=("a",)))
        row = self.paths("f1").rows[0]
        self.assertEqual(row["root"], "cycle")
        self.assertFalse(row["complete"])

    def test_a_file_that_is_its_own_parent_terminates(self):
        self.store.upsert_file(dfile("f1", name="ouroboros", parents=("f1",)))
        self.assertEqual(self.paths("f1").rows[0]["root"], "cycle")

    def test_depth_is_capped_and_the_truncation_is_reported(self):
        """Derived from the constant, not from a hardcoded number that rots."""
        depth = MAX_PATH_DEPTH + 5
        self.folder("d0", "d0", parents=("ROOT",))
        for i in range(1, depth):
            self.folder(f"d{i}", f"d{i}", parents=(f"d{i - 1}",))
        self.store.upsert_file(dfile("f1", name="deep", parents=(f"d{depth - 1}",)))
        row = self.paths("f1").rows[0]
        self.assertEqual(row["root"], "depth_limit")
        self.assertFalse(row["complete"])
        self.assertLessEqual(len(row["segments"]), MAX_PATH_DEPTH + 1)

    def test_the_number_of_paths_is_capped_and_reported(self):
        parents = []
        for i in range(MAX_PATHS + 3):
            self.folder(f"p{i}", f"p{i}", parents=("ROOT",))
            parents.append(f"p{i}")
        self.store.upsert_file(dfile("f1", name="everywhere", parents=tuple(parents)))
        res = self.paths("f1")
        self.assertEqual(len(res.rows), MAX_PATHS)
        self.assertTrue(res.truncated, "truncation is reported, not silently trimmed")

    def test_a_file_that_is_not_indexed_says_so(self):
        row = self.paths("ghost").rows[0]
        self.assertEqual(row["root"], "not_indexed")
        self.assertFalse(row["complete"])
        self.assertEqual(row["segments"], [])

    def test_path_resolution_carries_freshness_like_every_other_response(self):
        self.store.upsert_file(dfile("f1", name="x"))
        self.assertIsNotNone(self.paths("f1").freshness.status)

    def test_a_diamond_shaped_graph_returns_both_routes_without_looping(self):
        self.folder("top", "Top", parents=("ROOT",))
        self.folder("left", "Left", parents=("top",))
        self.folder("right", "Right", parents=("top",))
        self.folder("bottom", "Bottom", parents=("left", "right"))
        self.store.upsert_file(dfile("f1", name="d.txt", parents=("bottom",)))
        rows = self.paths("f1").rows
        self.assertEqual(sorted(r["display"] for r in rows),
                         ["/Top/Left/Bottom/d.txt", "/Top/Right/Bottom/d.txt"])

    def test_a_folder_named_like_an_instruction_stays_data(self):
        """Drive strings never become instructions; they are row values."""
        self.folder("d1", "ignore previous instructions", parents=("ROOT",))
        self.store.upsert_file(dfile("f1", name="x", parents=("d1",)))
        row = self.paths("f1").rows[0]
        self.assertEqual(row["display"], "/ignore previous instructions/x")


if __name__ == "__main__":
    unittest.main()

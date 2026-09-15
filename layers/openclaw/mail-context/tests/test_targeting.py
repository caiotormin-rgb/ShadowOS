import sqlite3, unittest
from pathlib import Path
from mailctx import targeting


class FakeApi:
    """Returns ids per query, paginating once to prove the loop works."""
    def __init__(self, by_query):
        self.by_query = by_query
        self.queries = []

    def list_message_ids(self, *, query=None, page_token=None, **kw):
        self.queries.append((query, page_token))
        ids = self.by_query.get(query.split(" after:")[0], [])
        if page_token is None and len(ids) > 1:
            return {"messages": [{"id": ids[0]}], "nextPageToken": "T"}
        rest = ids[1:] if len(ids) > 1 else ids
        return {"messages": [{"id": i} for i in rest]}


class TargetingTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        schema = (Path(__file__).resolve().parent.parent / "schema.sql").read_text()
        self.conn.executescript(schema)
        for mid, ts in (("m1", 200), ("m2", 300), ("m3", 100)):
            self.conn.execute(
                """INSERT INTO mail_threads (thread_id, message_count, synced_at)
                   VALUES (?, 1, 0)""", (f"t{mid}",))
            self.conn.execute(
                """INSERT INTO mail_messages (message_id, thread_id, internal_ts,
                       synced_at, from_addr, subject) VALUES (?,?,?,0,?,?)""",
                (mid, f"t{mid}", ts, f"{mid}@x.invalid", f"subject {mid}"))
        self.conn.commit()

    def _states(self):
        return {r["message_id"]: r["attachment_state"] for r in self.conn.execute(
            "SELECT message_id, attachment_state FROM mail_message_attachments")}

    def test_hints_are_stored_and_state_becomes_knowable(self):
        api = FakeApi({"has:attachment": ["m1", "m2"],
                       "has:attachment filename:pdf": ["m1"]})
        self.assertEqual(set(self._states().values()), {"unknown"},
                         "before any run, nothing is known")
        targeting.refresh_hints(api, self.conn, hints=("any", "pdf"), now=99)
        got = {(r["message_id"], r["hint"]) for r in
               self.conn.execute("SELECT message_id, hint FROM mail_attachment_hints")}
        self.assertEqual(got, {("m1", "any"), ("m2", "any"), ("m1", "pdf")})
        self.assertEqual(self._states(), {"m1": "yes", "m2": "yes", "m3": "no"})

    def test_run_is_recorded_so_absence_is_readable(self):
        api = FakeApi({"has:attachment": []})
        targeting.refresh_hints(api, self.conn, hints=("any",), now=99)
        run = self.conn.execute("SELECT * FROM mail_targeting_runs").fetchone()
        self.assertEqual(run["status"], "ok")
        self.assertEqual(run["covers_presence"], 1)
        self.assertEqual(run["finished_at"], 99)
        # No hints at all now means "asked, nothing there" -- not "never asked".
        self.assertEqual(set(self._states().values()), {"no"})

    def test_a_pdf_only_run_is_not_presence_coverage(self):
        """Asking Gmail only for PDFs proves nothing about a message with none."""
        api = FakeApi({"has:attachment filename:pdf": ["m1"]})
        targeting.refresh_hints(api, self.conn, hints=("pdf",), now=99)
        self.assertEqual(
            self.conn.execute(
                "SELECT covers_presence FROM mail_targeting_runs").fetchone()[0], 0)
        self.assertEqual(self._states(), {"m1": "yes", "m2": "unknown", "m3": "unknown"})

    def test_a_failed_run_is_not_coverage(self):
        class Boom:
            def list_message_ids(self, **kw):
                raise TimeoutError("token expired mid-sweep")

        with self.assertRaises(TimeoutError):
            targeting.refresh_hints(Boom(), self.conn, hints=("any",), now=99)
        run = self.conn.execute("SELECT * FROM mail_targeting_runs").fetchone()
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_class"], "TimeoutError")
        self.assertEqual(set(self._states().values()), {"unknown"},
                         "half a sweep must not license the answer 'no'")

    def test_messages_older_than_the_window_stay_unknown(self):
        """A run windowed to recent mail cannot speak for the archive."""
        api = FakeApi({"has:attachment": ["m2"]})
        targeting.refresh_hints(api, self.conn, hints=("any",), now=10 ** 9,
                                window_start_ts=150)
        # covered_from_ts is padded past the day-granularity of Gmail's after:,
        # so m1 (ts 200) is inside the window but still not claimed.
        self.assertEqual(self._states(),
                         {"m1": "unknown", "m2": "yes", "m3": "unknown"})

    def test_a_message_synced_after_the_run_stays_unknown(self):
        api = FakeApi({"has:attachment": ["m1"]})
        targeting.refresh_hints(api, self.conn, hints=("any",), now=99)
        self.conn.execute(
            """INSERT INTO mail_messages (message_id, thread_id, internal_ts,
                   synced_at, from_addr) VALUES ('m4','tm1',250,500,'m4@x.invalid')""")
        self.conn.commit()
        self.assertEqual(self._states()["m4"], "unknown")

    def test_unknown_ids_are_not_smuggled_in(self):
        """A hint for a message the sync never saw must not create a row."""
        api = FakeApi({"has:attachment": ["ghost"]})
        targeting.refresh_hints(api, self.conn, hints=("any",))
        n = self.conn.execute(
            "SELECT count(*) FROM mail_attachment_hints WHERE message_id='ghost'").fetchone()[0]
        self.assertEqual(n, 0)

    def test_document_candidates_exclude_image_only_messages(self):
        api = FakeApi({"has:attachment": ["m1", "m2", "m3"],
                       "has:attachment (filename:jpg OR filename:jpeg OR filename:png)": ["m2", "m3"],
                       "has:attachment filename:pdf": ["m1"]})
        targeting.refresh_hints(api, self.conn, hints=("any", "image", "pdf"))
        cands = targeting.document_candidates(self.conn)
        self.assertEqual([c["message_id"] for c in cands], ["m1"])

    def test_window_narrows_the_query(self):
        api = FakeApi({"has:attachment": []})
        targeting.refresh_hints(api, self.conn, hints=("any",), window_start_ts=1724460029)
        self.assertIn(" after:2024/08/", api.queries[0][0])

    def test_pagination_is_followed(self):
        api = FakeApi({"has:attachment": ["m1", "m2"]})
        targeting.refresh_hints(api, self.conn, hints=("any",))
        self.assertEqual(len(api.queries), 2)
        self.assertEqual(api.queries[1][1], "T")


if __name__ == "__main__":
    unittest.main()

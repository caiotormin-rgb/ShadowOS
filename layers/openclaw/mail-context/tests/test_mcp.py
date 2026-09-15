"""The agent boundary. Everything the chat agent can do is one of four tools."""
import io
import json
import tempfile
import unittest
from pathlib import Path

from mailctx import mcp
from mailctx.store import Store, connect, migrate
from tests.fixtures import T0, msg


class McpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db = Path(self.tmp.name) / "state" / "db.sqlite"
        conn = connect(db)
        migrate(conn)
        store = Store(conn)
        store.upsert_message(msg("m1", thread="t1", subject="Dentist appointment",
                                 snippet="Tuesday at 3", labels=("INBOX",)))
        store.advance_cursor(1, kind="initial")
        conn.close()
        self._real_db = mcp.DB_PATH
        mcp.DB_PATH = db
        self.addCleanup(lambda: setattr(mcp, "DB_PATH", self._real_db))

    def call(self, name, args=None):
        r = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": name, "arguments": args or {}}})
        return r["result"]

    # -- protocol --------------------------------------------------------
    def test_initialize_echoes_a_newer_protocol_version(self):
        """So a newer OpenClaw does not need a code change here."""
        r = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2099-01-01"}})
        self.assertEqual(r["result"]["protocolVersion"], "2099-01-01")
        self.assertEqual(r["result"]["serverInfo"]["name"], "mail-context")

    def test_initialize_without_a_version_uses_ours(self):
        r = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(r["result"]["protocolVersion"], mcp.PROTOCOL_VERSION)

    def test_notifications_get_no_response(self):
        self.assertIsNone(mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertIsNone(mcp.handle({"jsonrpc": "2.0", "method": "some/unknown/notification"}))

    def test_unknown_method_is_an_error_not_a_crash(self):
        r = mcp.handle({"jsonrpc": "2.0", "id": 7, "method": "nope"})
        self.assertEqual(r["error"]["code"], -32601)

    # -- the boundary ----------------------------------------------------
    def test_exactly_four_tools_are_exposed(self):
        names = {t["name"] for t in mcp.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]}
        self.assertEqual(names, {"mail_search", "mail_thread", "mail_candidates", "mail_status"})

    def test_no_tool_accepts_sql_a_path_or_a_write(self):
        """The agent must not be able to reach past the query contract."""
        banned = {"sql", "query_sql", "db", "db_path", "path", "file", "command", "exec"}
        for tool in mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]:
            props = set(tool["inputSchema"].get("properties", {}))
            self.assertEqual(props & banned, set(), f"{tool['name']} exposes {props & banned}")
            self.assertFalse(any(w in tool["name"] for w in ("send", "write", "delete", "create", "draft")),
                             f"{tool['name']} names a mutation")

    def test_tool_descriptions_tell_the_agent_bodies_are_absent(self):
        search = next(t for t in TOOLS_LIST() if t["name"] == "mail_search")
        self.assertIn("never message", search["description"])

    # -- behaviour -------------------------------------------------------
    def test_search_returns_rows_and_freshness(self):
        payload = json.loads(self.call("mail_search", {"query": "dentist"})["content"][0]["text"])
        self.assertEqual(payload["rows"][0]["message_id"], "m1")
        self.assertIn("description", payload["freshness"])

    def test_search_requires_a_string_query(self):
        r = self.call("mail_search", {"query": 123})
        self.assertTrue(r["isError"])

    def test_thread_requires_an_id(self):
        self.assertTrue(self.call("mail_thread", {})["isError"])

    def test_status_works(self):
        payload = json.loads(self.call("mail_status")["content"][0]["text"])
        self.assertEqual(payload["rows"][0]["messages"], 1)

    def test_unknown_tool_is_reported_not_raised(self):
        r = self.call("mail_evil_send", {})
        self.assertTrue(r["isError"])
        self.assertIn("Unknown tool", r["content"][0]["text"])

    def test_missing_index_is_explained_rather_than_crashing(self):
        mcp.DB_PATH = Path(self.tmp.name) / "nope.sqlite"
        r = self.call("mail_status")
        self.assertTrue(r["isError"])
        self.assertIn("not been created", r["content"][0]["text"])

    # -- transport -------------------------------------------------------
    def test_full_stdio_round_trip(self):
        lines = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "mail_search", "arguments": {"query": "dentist"}}}),
        ]
        out = io.StringIO()
        mcp.serve(stdin=io.StringIO("\n".join(lines) + "\n"), stdout=out)
        responses = [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]
        self.assertEqual([r["id"] for r in responses], [1, 2, 3],
                         "the notification must produce no response line")
        self.assertEqual(len(responses[1]["result"]["tools"]), 4)

    def test_malformed_line_does_not_kill_the_session(self):
        out = io.StringIO()
        mcp.serve(stdin=io.StringIO('not json\n{"jsonrpc":"2.0","id":9,"method":"ping"}\n'), stdout=out)
        responses = [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertEqual(responses[1]["id"], 9, "the session survives a bad line")


def TOOLS_LIST():
    return mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]


if __name__ == "__main__":
    unittest.main()

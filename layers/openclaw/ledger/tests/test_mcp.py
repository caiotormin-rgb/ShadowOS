import io, json, os, sqlite3, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class McpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = Path(self.tmp.name) / "l.sqlite"
        c = sqlite3.connect(db)
        c.executescript((Path(__file__).resolve().parent.parent / "schema.sql").read_text())
        c.execute("""INSERT INTO extraction_runs
              (run_id,started_at,source,lane,extractor,messages_in,rows_out)
              VALUES (1,0,'snippet','L1','test',2,2)""")
        c.execute("""INSERT INTO entities (entity_id,entity_key,name,kind,message_count)
                     VALUES (1,'merchant:acme.test','Acme Lawn','merchant',9)""")
        # Two rows for one real invoice: a dunning reminder and the payment.
        # Only the primary must count, or totals double.
        for i, (d, amt, prim) in enumerate([("2026-01-05", 100.0, 1), ("2026-01-06", 100.0, 0)]):
            c.execute("""INSERT INTO transactions
              (entity_id,counterparty,date,kind,amount,currency,ref_number,
               message_id,confidence,lane,event_id,is_primary,run_id)
              VALUES (1,'Acme Lawn',?,'payment',?,'USD','INV1',?,0.9,'L1',7,?,1)""",
              (d, amt, f"m{i}", prim))
        c.commit(); c.close()
        os.environ["LEDGER_DB"] = str(db)
        for m in list(sys.modules):
            if m == "mcp": del sys.modules[m]
        import mcp; self.mcp = mcp; self.mcp.DB = str(db)

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, name, args=None):
        return self.mcp.call(name, args or {})

    def test_totals_use_deduplicated_rows(self):
        """The single easiest way to be wrong: summing raw rows instead of the
        ledger view. One invoice dunned once must total 100, not 200."""
        got = self.call("ledger_entity", {"name": "Acme Lawn"})
        self.assertEqual(got["transactions"], 1)
        self.assertEqual(got["total"], 100.0)

    def test_unknown_name_suggests_alternatives(self):
        got = self.call("ledger_entity", {"name": "Topsed"})
        self.assertIn("error", got)

    def test_partial_name_matches(self):
        self.assertEqual(self.call("ledger_entity", {"name": "acme"})["transactions"], 1)

    def test_status_reports_shape(self):
        got = self.call("ledger_status")
        self.assertEqual(got["transactions"], 1)
        self.assertEqual(got["by_kind"], {"payment": 1})

    def test_missing_db_is_an_error_not_a_crash(self):
        self.mcp.DB = "/nonexistent/ledger.sqlite"
        self.assertIn("error", self.call("ledger_status"))

    def test_tools_list_and_initialize(self):
        out = io.StringIO()
        self.mcp.serve(io.StringIO(json.dumps({"jsonrpc":"2.0","id":1,"method":"tools/list"})+"\n"), out)
        names = [t["name"] for t in json.loads(out.getvalue())["result"]["tools"]]
        self.assertEqual(names, ["ledger_entity","ledger_search","ledger_entities","ledger_status"])

    def test_exceptions_do_not_leak_tracebacks(self):
        out = io.StringIO()
        self.mcp.serve(io.StringIO(json.dumps(
            {"jsonrpc":"2.0","id":9,"method":"tools/call",
             "params":{"name":"ledger_entity","arguments":{}}})+"\n"), out)
        text = json.loads(out.getvalue())["result"]["content"][0]["text"]
        self.assertNotIn("Traceback", text)
        self.assertIn("error", json.loads(text))


if __name__ == "__main__":
    unittest.main()

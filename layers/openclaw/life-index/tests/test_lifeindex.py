import json, sqlite3, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lifeindex import classify, fields, paths
from lifeindex.consume import consume_file, is_chrome
from lifeindex.store import (Artifact, Catalog, SchemaReconcileError, _column_defs,
                             _column_name, _declared, apply_schema, connect,
                             reconcile_columns, sha256_file)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        conn = connect(self.root / "catalog.sqlite")
        apply_schema(conn)
        self.cat = Catalog(conn, blob_root=self.root / "blobs")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        p = self.root / name
        p.write_text(text)
        return p


class FieldsTest(unittest.TestCase):
    # Synthetic identifiers, format-valid only. Real ones must never enter the
    # repo: it has a GitHub remote and git history is permanent.
    CONTRACT = ("CPF/MF nº 111.222.333-96 ... CNPJ: 12.345.678/0001-95 "
                "O preço é de R$ 550.000,00 e mobiliário R$ 8.000,00, entrada "
                "R$ 58.000,00 ... matrícula nº 136.896 ... Student ID: 52008107")

    def test_real_contract_values(self):
        got = dict(); [got.setdefault(k, []).append(v) for k, v in fields.extract_fields(self.CONTRACT)]
        self.assertEqual(got["cpf"], ["111.222.333-96"])
        self.assertEqual(got["cnpj"], ["12.345.678/0001-95"])
        self.assertEqual(got["matricula"], ["136.896"])

    def test_largest_amount_first(self):
        """A contract's price must outrank its instalments."""
        amounts = [v for k, v in fields.extract_fields(self.CONTRACT) if k == "amount_brl"]
        self.assertEqual(amounts[0], "R$ 550.000,00")

    def test_no_duplicate_values(self):
        text = "R$ 10,00 and again R$ 10,00"
        amounts = [v for k, v in fields.extract_fields(text) if k == "amount_brl"]
        self.assertEqual(amounts, ["R$ 10,00"])


class ClassifyTest(unittest.TestCase):
    def test_identity_documents_are_tier_1(self):
        for name in ("drivers-license.pdf", "Passport scan.pdf", "CNH.jpg"):
            with self.subTest(name=name):
                self.assertEqual(classify.classify(filename=name), 
                                 (("identity", 1)))

    def test_unknown_never_lands_in_tier_1(self):
        """Misfiling a receipt as identity is cheap; the reverse is not."""
        doc_type, tier = classify.classify(filename="scan001.pdf", text="hello")
        self.assertIsNone(doc_type)
        self.assertEqual(tier, 3)

    def test_filename_outranks_body_text(self):
        got = classify.classify(filename="W-2 2025.pdf", text="this mentions a receipt")
        self.assertEqual(got, ("tax", 1))

    def test_iep_is_education_tier_2(self):
        self.assertEqual(classify.classify(text="INDIVIDUALIZED EDUCATION PROGRAM"),
                         ("education", 2))


class ConsumeTest(Base):
    def test_plain_text_is_cataloged_with_fields(self):
        p = self.write("contract.txt", "escritura ... R$ 550.000,00 CPF 111.222.333-96")
        res = consume_file(p, self.cat)
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.doc_type, "contract")
        self.assertEqual(res.tier, 1)
        self.assertGreater(res.n_fields, 0)
        row = self.cat.get(res.sha256)
        self.assertTrue(any(f["key"] == "amount_brl" for f in row["fields"]))

    def test_duplicate_is_detected_by_content_not_name(self):
        a = self.write("a.txt", "identical bytes")
        b = self.write("b.txt", "identical bytes")
        self.assertEqual(consume_file(a, self.cat).status, "ok")
        self.assertEqual(consume_file(b, self.cat).status, "duplicate")

    def test_different_versions_are_kept_separate(self):
        """Draft and executed contract differ in bytes; both must survive."""
        a = self.write("draft.txt", "contrato v1")
        b = self.write("final.txt", "contrato v2 assinado")
        self.assertEqual(consume_file(a, self.cat).status, "ok")
        self.assertEqual(consume_file(b, self.cat).status, "ok")
        self.assertEqual(self.cat.conn.execute(
            "SELECT count(*) FROM artifacts").fetchone()[0], 2)

    def test_chrome_images_are_skipped(self):
        logo = self.root / "logo.png"
        logo.write_bytes(b"\x89PNG" + b"0" * 500)
        self.assertTrue(is_chrome(logo))
        self.assertEqual(consume_file(logo, self.cat).status, "skipped-chrome")

    def test_large_image_is_not_chrome(self):
        scan = self.root / "scan.jpg"
        scan.write_bytes(b"\xff\xd8" + b"0" * 200_000)
        self.assertFalse(is_chrome(scan))

    def test_blob_is_stored_and_readable(self):
        p = self.write("doc.txt", "keep me")
        res = consume_file(p, self.cat)
        blob = self.cat.blob_path(res.sha256)
        self.assertTrue(blob.exists())
        self.assertEqual(blob.read_text(), "keep me")
        self.assertEqual(sha256_file(blob), res.sha256)

    def test_empty_file_is_flagged_not_dropped(self):
        p = self.write("empty.pdf", "")
        res = consume_file(p, self.cat)
        row = self.cat.get(res.sha256)
        self.assertEqual(row["needs_review"], 1)
        self.assertIsNotNone(row["review_reason"])


class SearchTest(Base):
    def test_search_finds_by_body_text(self):
        p = self.write("x.txt", "the escritura for Rua Exemplo apartment 12")
        consume_file(p, self.cat)
        hits = self.cat.search("Exemplo")
        self.assertEqual(len(hits), 1)
        self.assertIn("Exemplo", hits[0]["excerpt"])

    def test_fts_operators_in_query_are_literals_not_syntax(self):
        """A bare * must not wildcard-match the corpus, and NOT/OR must not
        be honoured as operators."""
        self.write("a.txt", "ordinary content here")
        self.write("b.txt", "completely unrelated material")
        consume_file(self.root / "a.txt", self.cat)
        consume_file(self.root / "b.txt", self.cat)
        self.assertEqual(self.cat.search("*"), [])            # not "everything"
        self.assertEqual(self.cat.search('"*"'), [])
        # If NOT were honoured as syntax, 'content NOT here' would EXCLUDE the
        # document that contains both words. As literals it still matches it.
        hits = self.cat.search("content NOT here")
        self.assertEqual([h["original_name"] for h in hits], ["a.txt"])
        self.assertEqual(len(self.cat.search("ordinary")), 1)

    def test_reextraction_does_not_duplicate_hits(self):
        p = self.write("x.txt", "unique-token-here")
        res = consume_file(p, self.cat)
        self.cat.set_text(res.sha256, "unique-token-here again", extractor="pdftotext")
        self.assertEqual(len(self.cat.search("unique-token-here")), 1)


class MountGuardTest(unittest.TestCase):
    def test_unmounted_store_is_detected(self):
        """The guard must not be fooled by an ordinary empty directory."""
        with tempfile.TemporaryDirectory() as d:
            orig = paths.PLAIN
            try:
                paths.PLAIN = Path(d)
                self.assertFalse(paths.mounted())
                with self.assertRaises(SystemExit):
                    paths.require_mount()
            finally:
                paths.PLAIN = orig


if __name__ == "__main__":
    unittest.main()


class RegressionTest(Base):
    """Bugs found by running the pipeline over the real pilot documents."""

    def test_amount_wrapped_across_lines_has_no_newline(self):
        got = [v for k, v in fields.extract_fields("total de R$\n16.500,00 pagos")
               if k == "amount_brl"]
        self.assertEqual(got, ["R$ 16.500,00"])

    def test_hedged_bilingual_query_still_finds_the_document(self):
        p = self.write("letter.txt", "Overdue Immunizations Letter for the student")
        consume_file(p, self.cat)
        self.assertEqual(len(self.cat.search("imunizacao immunization")), 1)

    def test_precise_query_still_ands(self):
        self.write("a.txt", "escritura Exemplo apartment")
        self.write("b.txt", "escritura somewhere else entirely")
        consume_file(self.root / "a.txt", self.cat)
        consume_file(self.root / "b.txt", self.cat)
        self.assertEqual(len(self.cat.search("escritura Exemplo")), 1)

    def test_immunization_variants_all_classify_as_education(self):
        """Real documents say IMMUNIZATION / Immunizations / imunizacao;
        a word-anchored 'immuniz' matched none of them."""
        for text in ("NOTICE OF IMMUNIZATION", "Overdue Immunizations Letter",
                     "comprovante de imunizacao"):
            with self.subTest(text=text):
                self.assertEqual(classify.classify(text=text), ("education", 2))


class StoreModeTest(unittest.TestCase):
    """Plaintext must be an explicit choice, never a silent fallback."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig = (paths.STORE, paths.PLAIN, paths.PLAINTEXT_MARKER)
        paths.STORE = Path(self.tmp.name)
        paths.PLAIN = paths.STORE / "plain"
        paths.PLAINTEXT_MARKER = paths.STORE / "PLAINTEXT-UNENCRYPTED"
        paths.PLAIN.mkdir()

    def tearDown(self):
        paths.STORE, paths.PLAIN, paths.PLAINTEXT_MARKER = self.orig
        self.tmp.cleanup()

    def test_bare_directory_is_not_a_store(self):
        self.assertEqual(paths.mode(), "unavailable")
        with self.assertRaises(SystemExit):
            paths.require_store()

    def test_marker_enables_plaintext_writes(self):
        paths.PLAINTEXT_MARKER.write_text("acknowledged")
        self.assertEqual(paths.mode(), "plaintext")
        self.assertEqual(paths.require_store(), "plaintext")

    def test_encrypted_mode_does_not_need_the_marker(self):
        """mounted() is the authority; the marker only covers the plain case."""
        self.assertFalse(paths.plaintext_acknowledged())


class HarvestTest(Base):
    """The Gmail harvest layout must not be walked as a flat pile of files."""

    def _message(self, name, subject, files):
        d = self.root / name
        d.mkdir()
        (d / "meta.json").write_text(json.dumps(
            {"message_id": name, "category": "property", "subject": subject,
             "from": "someone@example.invalid"}))
        for fn, content in files.items():
            (d / fn).write_text(content)
        return d

    def test_meta_json_is_not_cataloged_as_a_document(self):
        """It contains the subject line, so content-classification files it as
        whatever the email was about. It is sidecar metadata, not a document."""
        from lifeindex.harvest import import_harvest
        self._message("m1", "COMPRA E VENDA contrato",
                      {"body.txt": "x" * 300, "deed.txt": "escritura content here"})
        import_harvest(self.root, self.cat)
        names = [r["original_name"] for r in self.cat.conn.execute(
            "SELECT original_name FROM artifacts")]
        self.assertNotIn("meta.json", names)

    def test_body_html_is_skipped_entirely(self):
        from lifeindex.harvest import import_harvest
        self._message("m2", "subject", {"body.txt": "y" * 300,
                                        "body.html": "<p>same content</p>"})
        import_harvest(self.root, self.cat)
        names = [r["original_name"] for r in self.cat.conn.execute(
            "SELECT original_name FROM artifacts")]
        self.assertFalse(any("body.html" in (n or "") for n in names))

    def test_email_about_a_contract_is_correspondence_not_a_contract(self):
        from lifeindex.harvest import import_harvest
        self._message("m3", "Re: escritura",
                      {"body.txt": "vamos assinar a escritura e o contrato " * 12})
        import_harvest(self.root, self.cat)
        row = self.cat.conn.execute(
            "SELECT doc_type, tier FROM artifacts").fetchone()
        self.assertEqual((row["doc_type"], row["tier"]), ("correspondence", 3))

    def test_attachment_keeps_its_own_type_and_tier(self):
        from lifeindex.harvest import import_harvest
        self._message("m4", "Re: escritura",
                      {"body.txt": "z" * 300,
                       "contrato.txt": "INSTRUMENTO PARTICULAR DE COMPROMISSO DE COMPRA E VENDA"})
        import_harvest(self.root, self.cat)
        row = self.cat.conn.execute(
            "SELECT doc_type, tier FROM artifacts WHERE original_name='contrato.txt'").fetchone()
        self.assertEqual((row["doc_type"], row["tier"]), ("contract", 1))


class McpTest(Base):
    def _call(self, name, args=None):
        import lifeindex.mcp as mcp
        orig_db, orig_marker = paths.DB, paths.PLAINTEXT_MARKER
        paths.DB = self.root / "catalog.sqlite"
        paths.PLAINTEXT_MARKER = self.root / "marker"
        paths.PLAINTEXT_MARKER.write_text("ack")
        try:
            return mcp.call(name, args or {})
        finally:
            paths.DB, paths.PLAINTEXT_MARKER = orig_db, orig_marker

    def test_tier1_text_is_withheld_from_the_agent(self):
        """The gateway is reachable from chat apps; identity and contract text
        does not go out by default."""
        p = self.write("deal.txt", "escritura ... R$ 550.000,00 CPF 111.222.333-96")
        res = consume_file(p, self.cat)
        got = self._call("artifact_get", {"sha256": res.sha256})
        self.assertNotIn("text", got)
        self.assertIn("text_withheld", got)

    def test_tier3_text_is_returned(self):
        p = self.write("note.txt", "just an ordinary note about nothing special")
        res = consume_file(p, self.cat)
        got = self._call("artifact_get", {"sha256": res.sha256})
        self.assertIn("text", got)

    def test_fields_are_queryable_across_documents(self):
        p = self.write("deal.txt", "contrato R$ 550.000,00")
        consume_file(p, self.cat)
        got = self._call("artifact_fields", {"key": "amount_brl"})
        self.assertTrue(any(f["value"] == "R$ 550.000,00" for f in got["fields"]))

    def test_status_reports_encryption_honestly(self):
        got = self._call("artifact_status")
        self.assertFalse(got["encrypted"])
        self.assertEqual(got["store_mode"], "plaintext")


class ProvenanceTest(Base):
    """Sender provenance is what makes the calibration report possible.

    The corpus measurement says the sender is the only durable template key
    (41 senders cover 50% of the non-marketing mail; subject keys are 82%
    singletons), so it has to survive into the catalog as a first-class row
    rather than being re-parsed out of `source_ref` later.
    """

    def _message(self, name, *, sender, subject="s", date=None, files=None,
                 thread="t-1"):
        d = self.root / name
        d.mkdir()
        (d / "meta.json").write_text(json.dumps(
            {"message_id": name, "thread_id": thread, "category": "human",
             "subject": subject, "from": sender, "date": date}))
        for fn, content in (files or {}).items():
            (d / fn).write_text(content)
        return d

    def test_sender_becomes_a_correspondent_row(self):
        from lifeindex.harvest import import_harvest
        self._message("m1", sender='"Sam Lima" <sam@school.org>',
                      files={"iep.txt": "INDIVIDUALIZED EDUCATION PROGRAM " * 10})
        import_harvest(self.root, self.cat)
        row = self.cat.conn.execute(
            "SELECT name, primary_addr FROM correspondents").fetchone()
        self.assertEqual((row["name"], row["primary_addr"]), ("Sam Lima", "sam@school.org"))

    def test_two_messages_from_one_sender_share_a_correspondent(self):
        from lifeindex.harvest import import_harvest
        self._message("m1", sender="sam@school.org", files={"a.txt": "alpha " * 60})
        self._message("m2", sender="sam@school.org", files={"b.txt": "beta " * 60})
        import_harvest(self.root, self.cat)
        self.assertEqual(self.cat.conn.execute(
            "SELECT count(*) FROM correspondents").fetchone()[0], 1)
        self.assertEqual(self.cat.conn.execute(
            "SELECT count(DISTINCT correspondent) FROM artifacts").fetchone()[0], 1)

    def test_mail_date_becomes_the_document_date_and_a_year_tag(self):
        from lifeindex.harvest import import_harvest
        self._message("m1", sender="a@x.com", date="Mon, 3 Feb 2025 10:00:00 -0300",
                      files={"doc.txt": "content " * 60})
        import_harvest(self.root, self.cat)
        row = self.cat.conn.execute(
            "SELECT sha256, doc_date FROM artifacts WHERE original_name='doc.txt'").fetchone()
        self.assertEqual(row["doc_date"], "2025-02-03")
        tags = {r["tag"] for r in self.cat.conn.execute(
            "SELECT tag FROM artifact_tags WHERE sha256=?", (row["sha256"],))}
        self.assertIn("year:2025", tags)
        self.assertIn("sender:a@x.com", tags)
        self.assertIn("domain:x.com", tags)

    def test_a_malformed_date_header_does_not_abort_the_import(self):
        """Real mail carries broken Date: headers, and one of them must not
        cost the other 2,999 messages."""
        from lifeindex.harvest import import_harvest
        self._message("m1", sender="a@x.com", date="not a date at all",
                      files={"doc.txt": "content " * 60})
        import_harvest(self.root, self.cat)
        row = self.cat.conn.execute(
            "SELECT doc_date FROM artifacts WHERE original_name='doc.txt'").fetchone()
        self.assertIsNone(row["doc_date"])

    def test_thread_id_from_the_fetcher_is_kept(self):
        from lifeindex.harvest import import_harvest
        self._message("m1", sender="a@x.com", thread="thread-42",
                      files={"doc.txt": "content " * 60})
        import_harvest(self.root, self.cat)
        self.assertEqual(self.cat.conn.execute(
            "SELECT thread_id FROM artifacts WHERE original_name='doc.txt'"
        ).fetchone()["thread_id"], "thread-42")

    def test_the_harvest_manifest_is_not_cataloged_as_a_document(self):
        from lifeindex.harvest import import_harvest
        d = self._message("m1", sender="a@x.com", files={"doc.txt": "content " * 60})
        (d / "manifest.json").write_text('{"a":1}')
        (d / "harvest.log").write_text("log line\n" * 40)
        import_harvest(self.root, self.cat)
        names = {r["original_name"] for r in self.cat.conn.execute(
            "SELECT original_name FROM artifacts")}
        self.assertEqual(names, {"doc.txt"})


class CalibrateTest(Base):
    """The report is the deliverable. These defend the claims it makes."""

    def _harvest_dir(self, name, *, sender, files, subject="s",
                     date="Mon, 3 Feb 2025 10:00:00 -0300"):
        d = self.root / "harvest" / name
        d.mkdir(parents=True)
        (d / "meta.json").write_text(json.dumps(
            {"message_id": name, "category": "human", "subject": subject,
             "from": sender, "date": date}))
        for fn, content in files.items():
            (d / fn).write_text(content)
        return d

    def _build(self, **kw):
        from lifeindex import calibrate
        from lifeindex.harvest import import_harvest
        import_harvest(self.root / "harvest", self.cat)
        return calibrate.build_report(self.cat, **kw)

    def test_report_ranks_senders_and_names_the_document_sender_first(self):
        self._harvest_dir("m1", sender="sam@school.org",
                          files={"iep.txt": "INDIVIDUALIZED EDUCATION PROGRAM " * 20})
        self._harvest_dir("m2", sender="news@bulk.com",
                          files={"body.txt": "newsletter text " * 40})
        report, senders, stats = self._build()
        self.assertEqual(senders[0].addr, "sam@school.org")
        self.assertIn("Senders, ranked", report)
        self.assertIn("sam@school.org", report)

    def test_a_sender_with_messages_and_no_documents_is_suggested_never(self):
        from lifeindex import calibrate
        row = calibrate.SenderRow("noise@x.com", messages_harvested=40)
        self.assertEqual(calibrate.suggest(row)[0], "never")

    def test_a_sender_with_a_tier1_document_is_never_suggested_never(self):
        """A wrong 'never' is silent and permanent. It must not be reachable
        for a sender that has produced an identity or contract document."""
        from lifeindex import calibrate
        row = calibrate.SenderRow("lawyer@x.com", messages_harvested=99,
                                  artifacts=1, tiers={1: 1}, doc_types={"contract": 1})
        self.assertEqual(calibrate.suggest(row)[0], "always")

    def test_email_bodies_do_not_count_as_documents(self):
        from lifeindex import calibrate
        row = calibrate.SenderRow("a@x.com", artifacts=5,
                                  doc_types={"correspondence": 5})
        self.assertEqual(row.real_documents, 0)

    def test_cost_curve_reports_decisions_not_messages(self):
        from lifeindex import calibrate
        rows = [calibrate.SenderRow("big@x.com", artifacts=90),
                calibrate.SenderRow("small@x.com", artifacts=10)]
        curve = calibrate.coverage_curve(rows)
        self.assertEqual(curve["50%"], 1)
        self.assertEqual(curve["95%"], 2)

    def test_manifest_supplies_the_noise_the_catalog_cannot_know(self):
        """Chrome is never cataloged, so only the manifest can count it."""
        self._harvest_dir("m1", sender="a@x.com", files={"doc.txt": "content " * 80})
        mpath = self.root / "manifest.jsonl"
        mpath.write_text(json.dumps({
            "message_id": "m1", "status": "ok", "sender": "a@x.com",
            "attachments": [{"filename": "doc.txt"}],
            "skipped": [{"filename": "logo.png", "reason": "chrome", "size": 9000},
                        {"filename": "sig.png", "reason": "chrome", "size": 1000}],
        }) + "\n" + json.dumps({
            "message_id": "m2", "status": "failed", "sender": "b@x.com",
            "error_class": "not_found", "attachments": [], "skipped": [],
        }) + "\n")
        report, senders, stats = self._build(manifest_path=mpath)
        self.assertEqual(stats["chrome_skipped"], 2)
        self.assertEqual(stats["chrome_bytes_avoided"], 10000)
        self.assertEqual(stats["fetch_failures"], 1)

    def test_a_sender_that_yielded_nothing_still_appears(self):
        """A message whose only attachments were logos produces no artifact.
        If the report cannot see it, the most actionable sender is invisible."""
        self._harvest_dir("m1", sender="keep@x.com", files={"doc.txt": "content " * 80})
        mpath = self.root / "manifest.jsonl"
        mpath.write_text("\n".join(json.dumps({
            "message_id": f"z{i}", "status": "ok", "sender": "allchrome@x.com",
            "attachments": [], "skipped": [{"filename": "l.png", "reason": "chrome",
                                            "size": 100}]}) for i in range(4)) + "\n")
        report, senders, stats = self._build(manifest_path=mpath)
        row = next(s for s in senders if s.addr == "allchrome@x.com")
        self.assertEqual(row.real_documents, 0)
        self.assertEqual(row.messages_harvested, 4)
        from lifeindex import calibrate
        self.assertEqual(calibrate.suggest(row)[0], "never")

    def test_sample_is_reproducible_for_a_seed(self):
        for i in range(12):
            self._harvest_dir(f"m{i}", sender=f"s{i}@x.com",
                              files={f"d{i}.txt": f"document {i} " * 40})
        a, _, _ = self._build(seed=7, sample_n=5)
        b, _, _ = self._build(seed=7, sample_n=5)
        self.assertEqual(a.split("Random sample")[1], b.split("Random sample")[1])

    def test_untyped_pile_is_reported_because_it_is_the_rules_gap(self):
        self._harvest_dir("m1", sender="a@x.com",
                          files={"mystery.txt": "wholly unrecognised content " * 30})
        report, _, stats = self._build()
        self.assertGreaterEqual(stats["untyped"], 1)
        self.assertIn("(untyped)", report)

    def test_levers_report_what_each_threshold_would_cost(self):
        self._harvest_dir("m1", sender="a@x.com", files={"d.txt": "content " * 80})
        report, _, _ = self._build()
        self.assertIn("Calibration levers", report)
        self.assertIn("drop artifacts under 20 KB", report)

    def test_verdicts_csv_has_one_editable_row_per_sender(self):
        from lifeindex import calibrate
        self._harvest_dir("m1", sender="sam@school.org",
                          files={"d.txt": "content " * 80})
        self._harvest_dir("m2", sender="bob@other.org",
                          files={"e.txt": "other " * 80})
        _, senders, _ = self._build()
        out = self.root / "v.csv"
        calibrate.write_verdicts_csv(out, senders)
        import csv as _csv
        with out.open() as f:
            rows = list(_csv.DictReader(f))
        self.assertEqual({r["sender"] for r in rows},
                         {"sam@school.org", "bob@other.org"})
        self.assertTrue(all(r["verdict"] == "" for r in rows))   # blank to fill in
        self.assertTrue(all(r["suggested"] for r in rows))

    def test_verdicts_csv_round_trips_into_the_selection_layer(self):
        """The loop has to close: the file Caio edits must be the file the
        next run's layer 0 reads."""
        import csv as _csv
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mail-context"))
        from mailctx import selection
        out = self.root / "v.csv"
        with out.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["sender", "verdict", "reason"])
            w.writerow(["noise@x.com", "never", "40 messages, 0 documents"])
            w.writerow(["sam@school.org", "always", ""])
        self.assertEqual(selection.load_verdicts(out),
                         {"noise@x.com": "never", "sam@school.org": "always"})


class SharedEntityIdentityTest(Base):
    """correspondents and ledger.entities are the same concept in two
    databases. They agree only if both carry the same entity_key."""

    def test_correspondents_carries_entity_key(self):
        cols = [d[1] for d in self.cat.conn.execute("PRAGMA table_info(correspondents)")]
        self.assertIn("entity_key", cols)

    def test_key_format_matches_the_resolver(self):
        """The key must be produced by the same function the ledger uses, or
        the two stores name the same party differently and never join."""
        import sys
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "mail-enrichment"))
        import entities as E
        key, name, kind, _ = E.resolve('"Acme Imoveis" <contato@acme-imoveis.com.br>')
        self.cat.conn.execute(
            "INSERT INTO correspondents (entity_key,name,kind) VALUES (?,?,?)",
            (key, name, "org"))
        self.cat.conn.commit()
        got = self.cat.conn.execute(
            "SELECT entity_key FROM correspondents").fetchone()["entity_key"]
        self.assertEqual(got, key)
        self.assertTrue(got.startswith(("merchant:", "person:", "self:")))


class PointerArtifactTest(Base):
    """A tier-1 document cataloged as metadata-only must still be findable.
    Otherwise the catalog answers "where is my W-2" only if you already know
    its hash — which is not an answer."""

    def _pointer(self, name, title, doc_type="tax", tier=1):
        from lifeindex.store import Artifact
        digest = "a" * 63 + str(tier)
        self.cat.upsert(Artifact(sha256=digest, bytes=0, mime="application/pdf",
                                 ext=".pdf", source="drive", source_ref="fid",
                                 original_name=name, title=title,
                                 doc_type=doc_type, tier=tier))
        return digest

    def test_metadata_only_artifact_is_searchable(self):
        self._pointer("Google LLC-2019-W2.pdf", "Google LLC 2019 W2")
        self.assertEqual(len(self.cat.search("W2")), 1)

    def test_pointer_stores_no_text(self):
        d = self._pointer("Google LLC-2019-W2.pdf", "Google LLC 2019 W2")
        n = self.cat.conn.execute(
            "SELECT count(*) FROM artifact_text WHERE sha256=?", (d,)).fetchone()[0]
        self.assertEqual(n, 0)
        self.assertIsNone(self.cat.search("W2")[0]["excerpt"] or None)

    def test_findable_by_filename_too(self):
        self._pointer("Scan Mar 12 2020.pdf", "identity card")
        self.assertEqual(len(self.cat.search("Scan")), 1)

    def test_every_artifact_gets_exactly_one_fts_row(self):
        p = self.write("doc.txt", "some content here")
        from lifeindex.consume import consume_file
        res = consume_file(p, self.cat)
        n = self.cat.conn.execute(
            "SELECT count(*) FROM artifact_search WHERE sha256=?", (res.sha256,)).fetchone()[0]
        self.assertEqual(n, 1)


SCHEMA = Path(__file__).resolve().parent.parent / "schema.sql"

# The real pre-entity_key schema, taken from git rather than approximated by
# hand: a test that migrates a made-up "old" shape only proves the made-up
# shape works.
SCHEMA_V0 = "b325898:layers/openclaw/life-index/schema.sql"


def old_schema():
    import subprocess
    r = subprocess.run(["git", "-C", str(SCHEMA.parent), "show", SCHEMA_V0],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise unittest.SkipTest("no git history here")
    return r.stdout


class ReconcileTest(unittest.TestCase):
    """apply_schema must bring a store that predates a column up to date.

    schema.sql is all CREATE ... IF NOT EXISTS, so it alone only ever builds
    from nothing: against an existing table the CREATE is skipped, the column
    never lands, and the index on it dies with "no such column". That aborts
    apply_schema, which every write path calls, so the store goes unwritable.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "c.sqlite"

    def store(self, sql, rows=True):
        conn = connect(self.path)
        conn.executescript(sql)
        if rows:
            conn.execute(
                "INSERT INTO correspondents (name,kind) VALUES ('Acme Imoveis','org')")
            conn.execute(
                """INSERT INTO artifacts (sha256,bytes,mime,ext,source,title,tier,
                                          needs_review,created_at,updated_at)
                   VALUES (?,10,'text/plain','txt','manual','Contract',1,0,1,1)""",
                ("a" * 64,))
        conn.commit()
        return conn

    # -- the failure this exists to prevent -------------------------------
    def test_naive_executescript_still_fails(self):
        """Guards the premise. If this ever stops raising, the schema no longer
        adds columns to existing tables and the rest of this class is moot."""
        conn = self.store(old_schema())
        with self.assertRaises(sqlite3.OperationalError) as e:
            conn.executescript(SCHEMA.read_text())
        self.assertIn("entity_key", str(e.exception))

    def test_apply_schema_migrates_a_real_old_store(self):
        conn = self.store(old_schema())
        apply_schema(conn)              # must not raise
        cols = {r[1] for r in conn.execute("PRAGMA table_info(correspondents)")}
        self.assertIn("entity_key", cols)

    def test_existing_rows_survive(self):
        """ADD COLUMN never rewrites a table; assert that rather than trust it."""
        conn = self.store(old_schema())
        apply_schema(conn)
        row = conn.execute("SELECT name, entity_key FROM correspondents").fetchone()
        self.assertEqual(row["name"], "Acme Imoveis")
        self.assertIsNone(row["entity_key"])
        self.assertEqual(
            conn.execute("SELECT title FROM artifacts").fetchone()["title"], "Contract")

    def test_migrated_store_is_writable(self):
        """The bug's real cost was an unwritable store, not a missing column."""
        conn = self.store(old_schema())
        apply_schema(conn)
        cat = Catalog(conn, blob_root=Path(self.tmp.name) / "blobs")
        cat.upsert(Artifact(sha256="b" * 64, bytes=4, mime="text/plain", ext="txt",
                            source="manual", title="Lease"))
        self.assertEqual(len(cat.search("Lease")), 1)

    # -- the UNIQUE column SQLite cannot ALTER in --------------------------
    def test_unique_constraint_survives_migration(self):
        """entity_key is declared UNIQUE, and SQLite cannot ADD a UNIQUE column.
        A migrated store must still reject duplicates, or cross-store entity
        joins silently fan out."""
        conn = self.store(old_schema())
        apply_schema(conn)
        conn.execute("UPDATE correspondents SET entity_key='merchant:example.com'")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO correspondents (entity_key,name,kind)"
                         " VALUES ('merchant:example.com','Other','org')")

    def test_unique_index_name_matches_the_hand_migration(self):
        """The live store was fixed by hand on 2026-08-25 with this index name.
        Generating the same name is what keeps this a no-op there."""
        conn = self.store(old_schema())
        apply_schema(conn)
        names = {r[1] for r in conn.execute("PRAGMA index_list(correspondents)")}
        self.assertIn("correspondents_entity_key_uq", names)

    # -- idempotence -------------------------------------------------------
    def test_reconcile_is_a_noop_on_a_current_store(self):
        conn = self.store(SCHEMA.read_text())
        self.assertEqual(reconcile_columns(conn, SCHEMA.read_text()), [])

    def test_apply_schema_twice_changes_nothing(self):
        conn = self.store(old_schema())
        apply_schema(conn)
        before = [tuple(r) for r in conn.execute("PRAGMA table_info(correspondents)")]
        apply_schema(conn)
        after = [tuple(r) for r in conn.execute("PRAGMA table_info(correspondents)")]
        self.assertEqual(before, after)

    def test_empty_database_needs_no_reconcile(self):
        """Nothing on disk yet: CREATE TABLE builds it, reconcile stays out."""
        conn = connect(self.path)
        self.assertEqual(reconcile_columns(conn, SCHEMA.read_text()), [])
        apply_schema(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(correspondents)")}
        self.assertIn("entity_key", cols)

    # -- the point of deriving from schema.sql instead of a hand list ------
    def test_a_column_added_only_to_schema_sql_is_picked_up(self):
        """The whole design goal. Nobody edits a migration list; the column is
        declared once and the reconcile finds it."""
        conn = self.store(SCHEMA.read_text())
        future = SCHEMA.read_text().replace(
            "  primary_addr  TEXT,",
            "  primary_addr  TEXT,\n  vat_id        TEXT,")
        applied = reconcile_columns(conn, future)
        self.assertEqual(applied, ["ALTER TABLE correspondents ADD COLUMN vat_id TEXT"])
        self.assertIn("vat_id",
                      {r[1] for r in conn.execute("PRAGMA table_info(correspondents)")})

    def test_check_and_references_clauses_survive_into_the_alter(self):
        """Rebuilding the column from PRAGMA table_info would drop these; the
        DDL text is carried through verbatim for exactly that reason."""
        conn = self.store(SCHEMA.read_text())
        conn.execute("ALTER TABLE artifacts DROP COLUMN executed")
        conn.commit()
        applied = reconcile_columns(conn, SCHEMA.read_text())
        self.assertEqual(len(applied), 1)      # not a vacuous pass
        ddl = conn.execute("SELECT sql FROM sqlite_master"
                           " WHERE name='artifacts'").fetchone()["sql"]
        self.assertIn("CHECK (executed IN (0,1))", ddl)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE artifacts SET executed = 7")

    # -- guard rails -------------------------------------------------------
    def test_not_null_without_default_is_named_not_guessed(self):
        """SQLite cannot add it. Fail with the table, the column and the reason
        rather than the bare 'no such column' this class exists to kill."""
        conn = self.store(SCHEMA.read_text())
        future = SCHEMA.read_text().replace(
            "  primary_addr  TEXT,", "  primary_addr  TEXT,\n  region TEXT NOT NULL,")
        with self.assertRaises(SchemaReconcileError) as e:
            reconcile_columns(conn, future)
        self.assertIn("correspondents.region", str(e.exception))
        self.assertIn("NOT NULL", str(e.exception))

    def test_not_null_with_default_is_fine(self):
        conn = self.store(SCHEMA.read_text())
        future = SCHEMA.read_text().replace(
            "  primary_addr  TEXT,",
            "  primary_addr  TEXT,\n  region TEXT NOT NULL DEFAULT 'br',")
        reconcile_columns(conn, future)
        self.assertEqual(
            conn.execute("SELECT region FROM correspondents").fetchone()["region"], "br")

    # -- parser --------------------------------------------------------------
    def test_inline_comments_do_not_split_columns(self):
        """schema.sql documents columns inline and those comments carry commas
        and parens ('-- 0 deterministic, 1 local, 3 cloud'). Splitting on a raw
        ',' produces garbage DDL from mid-sentence."""
        ddl = _declared(SCHEMA.read_text())["extraction_runs"][0]
        cols = [c for c in (_column_name(f) for f in _column_defs(ddl)) if c]
        self.assertEqual(cols, ["run_id", "lane", "model", "version",
                                "started_at", "finished_at", "items"])

    def test_table_constraints_are_not_mistaken_for_columns(self):
        """correspondents ends with UNIQUE (name, kind) -- a table constraint,
        not a column to ALTER in."""
        ddl = _declared(SCHEMA.read_text())["correspondents"][0]
        cols = [c for c in (_column_name(f) for f in _column_defs(ddl)) if c]
        self.assertEqual(cols, ["id", "entity_key", "name", "kind", "primary_addr"])

    def test_fts_shadow_tables_are_left_alone(self):
        """artifact_search is FTS5; its shadow tables belong to the extension
        and their shape tracks its version, not ours."""
        declared = _declared(SCHEMA.read_text())
        self.assertNotIn("artifact_search", declared)
        self.assertFalse([t for t in declared if t.startswith("artifact_search_")])

import json
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli  # noqa: E402
import render  # noqa: E402
import store  # noqa: E402
from store import DoctorError  # noqa: E402

ANA, BIA = "+15550100001", "+15550100002"
INTAKE = {"patient": "son, 7", "zip": "10001", "visit_type": "visit",
          "specialty_text": "child psychologist for autism", "availability": "weekday afternoons",
          "plan_name": "ExamplePlan"}
CAND = {"id": "kidsmind.example", "verified": True, "name": "Kids Mind Psychology", "org": "Kids Mind Psychology",
        "address": "1 Main St, Hackensack, NJ 10001", "zip": "10001", "miles": 0.4, "phone": "201-555-0100",
        "emails": ["hello@kidsmind.example"], "contact_form_url": "", "booking_url": "",
        "url": ["https://kidsmind.example"], "match": 3, "match_evidence": "Autism evaluations for children 2-17",
        "insurance": "in_network", "insurance_evidence": "We accept UnitedHealthcare", "new_patients": "yes",
        "telehealth": "no", "languages": [], "rating": {"avg": 4.8, "count": 120, "sources": ["google"]}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "d.sqlite3"
        self.conn = store.connect(self.db)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def error(self, fn):
        with self.assertRaises(DoctorError) as e:
            fn()
        return e.exception.code

    def queued(self, who=ANA, lang="en", **fields):
        rid = store.new_request(self.conn, who, lang)
        store.set_intake(self.conn, rid, who, {**INTAKE, **fields})
        store.confirm_intake(self.conn, rid, who)
        return rid


class StepTest(Base):
    def test_steps_cannot_be_skipped(self):
        rid = store.new_request(self.conn, ANA, "en")
        self.assertEqual(self.error(lambda: store.confirm_intake(self.conn, rid, ANA)), "intake_incomplete")
        self.assertEqual(self.error(lambda: store.enqueue(self.conn, rid, ANA)), "wrong_step")
        self.assertEqual(self.error(lambda: store.choose(self.conn, rid, ANA, [1])), "wrong_step")
        self.assertIsNone(store.claim_job(self.conn))

    def test_confirm_queues_one_job_at_a_time(self):
        rid = self.queued()
        self.assertEqual(store.view(self.conn, rid, ANA)["search"]["status"], "queued")
        self.assertEqual(self.error(lambda: store.enqueue(self.conn, rid, ANA)), "already_running")
        job = store.claim_job(self.conn)
        self.assertEqual((job["id"], job["requester"], job["lang"]), (rid, ANA, "en"))
        self.assertIsNone(store.claim_job(self.conn))  # running, not stale
        store.fail_job(self.conn, rid, "llm_failed")
        self.assertEqual(store.view(self.conn, rid, ANA)["search"]["status"], "failed")
        store.enqueue(self.conn, rid, ANA)
        self.assertEqual(store.claim_job(self.conn)["id"], rid)

    def test_stuck_job_is_retried_once(self):
        rid = self.queued()
        store.claim_job(self.conn)
        meta = store.view(self.conn, rid, ANA)["search"]
        meta["started_at"] = time.time() - 2 * store.STALE_JOB_SECONDS
        self.conn.execute("UPDATE requests SET search=? WHERE id=?", (json.dumps(meta), rid))
        self.conn.commit()
        self.assertEqual(store.claim_job(self.conn)["id"], rid)
        meta = store.view(self.conn, rid, ANA)["search"]
        meta["started_at"] = time.time() - 2 * store.STALE_JOB_SECONDS
        self.conn.execute("UPDATE requests SET search=? WHERE id=?", (json.dumps(meta), rid))
        self.conn.commit()
        self.assertIsNone(store.claim_job(self.conn))  # two attempts used

    def test_results_move_to_shortlist_or_stay_empty(self):
        rid = self.queued()
        store.claim_job(self.conn)
        store.save_search(self.conn, rid, ANA, [], {"status": "empty"})
        req = store.view(self.conn, rid, ANA)
        self.assertEqual((req["step"], req["search"]["status"]), ("search", "empty"))
        store.enqueue(self.conn, rid, ANA)
        store.claim_job(self.conn)
        store.save_search(self.conn, rid, ANA, [CAND], {"status": "done"})
        self.assertEqual(store.view(self.conn, rid, ANA)["step"], "shortlist")
        store.choose(self.conn, rid, ANA, [1])
        text = render.summary(store.view(self.conn, rid, ANA), "pt")
        self.assertIn("Kids Mind Psychology", text)
        self.assertIn("Aceita ExamplePlan", text)

    def test_widen_requeues_with_new_area(self):
        rid = self.queued()
        store.claim_job(self.conn)
        store.save_search(self.conn, rid, ANA, [CAND], {"status": "done"})
        store.reopen_search(self.conn, rid, ANA, {"miles": 30})
        req = store.view(self.conn, rid, ANA)
        self.assertEqual((req["step"], req["search"]["status"], req["intake"]["max_miles"]), ("search", "queued", 30.0))
        self.assertEqual(req["candidates"], [])


class GuardTest(Base):
    def test_other_requester_sees_nothing(self):
        rid = self.queued()
        for action in (lambda: store.view(self.conn, rid, BIA),
                       lambda: store.set_intake(self.conn, rid, BIA, {"zip": "10001"}),
                       lambda: store.enqueue(self.conn, rid, BIA),
                       lambda: store.close(self.conn, rid, BIA)):
            self.assertEqual(self.error(action), "not_found")
        self.assertEqual(store.list_requests(self.conn, BIA), [])

    def test_intake_rejects_bad_and_sensitive_fields(self):
        rid = store.new_request(self.conn, ANA, "pt")
        for fields in ({"member_id": "X123"}, {"dob": "2016-01-01"}, {"zip": "0760"},
                       {"visit_type": "surgery"}, {"miles": 500}, {"context": "x" * 301}):
            self.assertEqual(self.error(lambda: store.set_intake(self.conn, rid, ANA, fields)), "bad_field")
        store.set_intake(self.conn, rid, ANA, {**INTAKE, "zip": "00000"})
        self.assertEqual(self.error(lambda: store.confirm_intake(self.conn, rid, ANA)), "bad_field")

    def test_purge_after_90_days(self):
        old = self.queued()
        store.close(self.conn, old, ANA)
        recent = self.queued()
        later = time.time() + 91 * 86400
        self.conn.execute("UPDATE requests SET updated_at=? WHERE id=?", (later, recent))
        self.conn.commit()
        self.assertEqual(store.purge(self.conn, now=later)["requests"], 1)
        self.assertEqual(self.error(lambda: store.view(self.conn, old, ANA)), "not_found")
        store.view(self.conn, recent, ANA)


class CliTest(Base):
    def run_cli(self, *args):
        with mock.patch("sys.stdout") as stdout:
            code = cli.main(["--db", str(self.db), *args])
        return code, "".join(call.args[0] for call in stdout.write.call_args_list)

    def test_errors_are_json_and_intake_text(self):
        code, text = self.run_cli("new", "--requester", ANA, "--lang", "pt")
        rid = json.loads(text)["id"]
        code, text = self.run_cli("intake", "--requester", ANA, "--id", rid, "--fields", '{"member_id": "1"}')
        self.assertEqual((code, json.loads(text)["error"]), (2, "bad_field"))
        code, text = self.run_cli("intake", "--requester", ANA, "--id", rid,
                                  "--fields", '{"patient": "eu", "zip": "10001"}', "--format", "text")
        self.assertEqual(code, 0)
        self.assertIn("Ainda falta", text)

    def test_draft_code_goes_to_requester_not_output(self):
        rid = self.queued(lang="pt")
        store.claim_job(self.conn)
        store.save_search(self.conn, rid, ANA, [CAND], {"status": "done"})
        store.choose(self.conn, rid, ANA, [1])
        self.conn.execute("INSERT INTO contact_emails VALUES (?,?,NULL,NULL,0,?)", (ANA, "me@example.com", time.time()))
        self.conn.commit()
        args = ("draft", "--requester", ANA, "--id", rid, "--rank", "1", "--to", "hello@kidsmind.example", "--name", "Sam")
        sent = []
        with mock.patch.object(cli.notify, "whatsapp", lambda to, text: sent.append((to, text))):
            code, text = self.run_cli(*args)
        self.assertEqual(code, 0)
        self.assertNotIn("approval_code", json.loads(text))
        nonce = re.search(r"/ok ([0-9A-F]{6})", sent[0][1]).group(1)
        self.assertNotIn(nonce, text)
        self.assertEqual(sent[0][0], ANA)

        def down(to, message):
            raise DoctorError("notify_failed", "down")
        with mock.patch.object(cli.notify, "whatsapp", down):
            code, text = self.run_cli(*args)
        self.assertEqual(json.loads(text)["error"], "notify_failed")
        statuses = [r[0] for r in self.conn.execute("SELECT status FROM outreach ORDER BY id")]
        self.assertEqual(statuses, ["cancelled", "cancelled"])


if __name__ == "__main__":
    unittest.main()

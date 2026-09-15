import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import curate  # noqa: E402
import llm  # noqa: E402
import store  # noqa: E402
import web  # noqa: E402
from store import DoctorError  # noqa: E402

ANA = "+15550100001"
INTAKE = {"patient": "filho, 7", "zip": "10001", "visit_type": "visit",
          "specialty_text": "psicólogo infantil especializado em autismo", "availability": "tardes",
          "plan_name": "ExamplePlan", "miles": 10}


def wrap(text):
    return f'\n<<<EXTERNAL_UNTRUSTED_CONTENT id="abc">>>\nSource: Web Fetch\n---\n{text}\n<<<END_EXTERNAL_UNTRUSTED_CONTENT id="abc">>>'


SITES = {
    "https://www.kidsmind.example/autism": (
        "# Kids Mind Psychology\nAutism evaluations and therapy for children ages 2-17.\n"
        "[Insurance](https://www.kidsmind.example/insurance) [Blog](https://www.kidsmind.example/blog)"),
    "https://www.kidsmind.example/insurance": "We are in network with UnitedHealthcare and Aetna.",
    "https://www.faraway.example/": "# Far Away Child Psychology\nAutism testing for kids. Los Angeles, CA 90012",
    "https://blog.example/autism-signs": "# 10 signs of autism\nAn article.",
    "https://www.adults.example/": "# Adult Therapy Center\nAnxiety and depression for adults.",
    "https://www.second.example/": "# Second Step Pediatric Psychology\nWe evaluate autism in children. Not accepting new patients.",
    "https://www.nozip.example/": "# Somewhere Kids Clinic\nAutism therapy for children.",
    "https://www.addressonly.example/": "# Bergen Child Psych\nAutism evaluations for kids.",
}
EXTRACTS = {
    "kidsmind.example": {"is_practice": True, "name": "Kids Mind Psychology", "address": "1 Main St, Hackensack, NJ",
                         "zip": "10001", "phone": "201-555-0100", "emails": ["hello@kidsmind.example", "not an email"],
                         "contact_form_url": "", "booking_url": "https://kidsmind.example/book", "match": 3,
                         "match_evidence": "Autism evaluations and therapy for children ages 2-17",
                         "insurance": "in_network", "insurance_evidence": "in network with UnitedHealthcare",
                         "new_patients": "yes", "telehealth": "no", "languages": []},
    "faraway.example": {"is_practice": True, "name": "Far Away", "zip": "90012", "match": 3, "insurance": "unknown",
                        "new_patients": "yes"},
    "blog.example": {"is_practice": False, "name": "Blog", "match": 0},
    "adults.example": {"is_practice": True, "name": "Adult Therapy Center", "zip": "10001", "match": 1},
    "second.example": {"is_practice": True, "name": "Second Step Pediatric Psychology", "zip": "10002", "phone": "201-555-0199",
                       "match": 3, "match_evidence": "We evaluate autism in children", "insurance": "maybe",
                       "new_patients": "no", "telehealth": "yes"},
    # No ZIP and no address to find one in: could be anywhere, so it is left out.
    "nozip.example": {"is_practice": True, "name": "Somewhere Kids Clinic", "zip": "", "address": "Torrance, CA",
                      "match": 3, "insurance": "in_network", "new_patients": "yes"},
    # ZIP only inside the address: still placed.
    "addressonly.example": {"is_practice": True, "name": "Bergen Child Psych", "zip": "",
                            "address": "10 Essex St, Hackensack, NJ 10001-4321", "match": 2,
                            "insurance": "unknown", "new_patients": "unknown"},
}


class FakeOpenClaw:
    def __init__(self):
        self.calls = []
        self.plan = {"need": "Psychologist who evaluates and treats autism in a 7-year-old child",
                     "search_terms": ["child psychologist autism evaluation", "pediatric autism therapy"]}

    def __call__(self, cmd, **_):
        args = cmd[1:]
        self.calls.append(args)
        value = lambda flag: next(a.split("=", 1)[1] for a in args if a.startswith(flag + "="))
        if args[:3] == ["infer", "web", "search"]:
            query = value("--query")
            if query.endswith("reviews"):
                results = [{"url": "https://www.google.com/maps/kids", "title": "Kids Mind Psychology",
                            "description": "4.9 out of 5 stars · 212 reviews"},
                           {"url": "https://www.kidsmind.example/testimonials", "title": "Testimonials",
                            "description": "5 stars 1000 reviews"}] if "Kids Mind" in query else []
            else:
                results = [{"url": "https://www.psychologytoday.com/us/autism", "title": "Directory"}]
                results += [{"url": url, "title": wrap("t"), "description": wrap("d")} for url in SITES
                            if url != "https://www.kidsmind.example/insurance"]
            payload = {"outputs": [{"result": {"results": results}}]}
        elif args[:3] == ["infer", "web", "fetch"]:
            url = value("--url")
            if url not in SITES:
                return SimpleNamespace(returncode=1, stdout="", stderr="404")
            payload = {"outputs": [{"result": {"url": url, "finalUrl": url, "status": 200, "text": wrap(SITES[url])}}]}
        elif args[:3] == ["infer", "model", "run"]:
            prompt = value("--prompt")
            if "write\nweb search phrases" in prompt or "search phrases" in prompt.split("Request")[0]:
                answer = json.dumps(self.plan)
            else:
                assert "<<<" not in prompt, "untrusted wrappers must be stripped"
                page = prompt.split("=== PAGE ", 1)[1].split(" ===", 1)[0]
                answer = "```json\n" + json.dumps(EXTRACTS[curate.domain_of(page)]) + "\n```"
            payload = {"outputs": [{"text": answer}]}
        else:
            raise AssertionError(args)
        return SimpleNamespace(returncode=0, stdout="🦞 banner\n" + json.dumps(payload), stderr="")


class CurateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.connect(Path(self.tmp.name) / "d.sqlite3")
        self.fake = FakeOpenClaw()
        patches = [mock.patch.object(web, "RUN", self.fake), mock.patch.object(llm, "RUN", self.fake)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.rid = store.new_request(self.conn, ANA, "pt")
        store.set_intake(self.conn, self.rid, ANA, INTAKE)
        store.confirm_intake(self.conn, self.rid, ANA)
        self.sent = []

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def run_jobs(self):
        return curate.run_pending(self.conn, lambda to, text: self.sent.append((to, text)))

    def test_curates_ranks_and_notifies(self):
        [job] = self.run_jobs()
        self.assertEqual(job["status"], "done")
        req = store.view(self.conn, self.rid, ANA)
        self.assertEqual(req["step"], "shortlist")
        names = [c["name"] for c in req["candidates"]]
        self.assertEqual(names, ["Kids Mind Psychology", "Bergen Child Psych", "Second Step Pediatric Psychology"])
        kids, placed, second = req["candidates"]
        self.assertEqual((placed["zip"], placed["miles"]), ("10001", 0.0))
        self.assertEqual(kids["emails"], ["hello@kidsmind.example"])
        self.assertEqual(kids["rating"]["sources"], ["google"])  # own-site testimonials ignored
        self.assertGreater(kids["rating"]["avg"], 4.7)
        self.assertEqual(second["insurance"], "unknown")  # unexpected value normalized
        self.assertEqual(second["new_patients"], "no")
        fetched = [a for a in self.fake.calls if a[:3] == ["infer", "web", "fetch"]]
        self.assertIn("--url=https://www.kidsmind.example/insurance", [x for a in fetched for x in a])
        self.assertFalse(any("psychologytoday" in x for a in fetched for x in a))
        [(to, text)] = self.sent
        self.assertEqual(to, ANA)
        self.assertIn("encontrei 3 opções", text)
        self.assertNotIn("Somewhere", text)
        self.assertIn("Aceita ExamplePlan", text)
        self.assertIn("★ 4.9", text)
        self.assertIn("agendamento online", text)

    def test_second_run_uses_cache(self):
        self.run_jobs()
        web_calls = sum(1 for a in self.fake.calls if a[:2] == ["infer", "web"])
        store.reopen_search(self.conn, self.rid, ANA, {"miles": 10})
        self.run_jobs()
        self.assertEqual(sum(1 for a in self.fake.calls if a[:2] == ["infer", "web"]), web_calls)

    def test_failure_is_reported_and_retryable(self):
        self.fake.plan = "not json"
        with mock.patch.object(llm, "parse_json", side_effect=ValueError):
            [job] = self.run_jobs()
        self.assertEqual((job["status"], job["reason"]), ("failed", "llm_failed"))
        self.assertIn("não terminou", self.sent[0][1])
        store.enqueue(self.conn, self.rid, ANA)
        self.fake.plan = {"need": "x", "search_terms": ["child psychologist autism"]}
        [job] = self.run_jobs()
        self.assertEqual(job["status"], "done")

    def test_nothing_found_stays_at_search(self):
        with mock.patch.dict(EXTRACTS, {k: {"is_practice": False} for k in EXTRACTS}):
            [job] = self.run_jobs()
        self.assertEqual(job["status"], "empty")
        req = store.view(self.conn, self.rid, ANA)
        self.assertEqual((req["step"], req["search"]["status"]), ("search", "empty"))
        self.assertIn("área maior", self.sent[0][1])


class HelperTest(unittest.TestCase):
    def test_links_and_domains(self):
        text = ("[Contact us](https://www.x.example/contact) [Insurance we take](https://www.x.example/fees) "
                "[Facebook](https://facebook.com/x) [Home](https://www.x.example/)")
        self.assertEqual(curate.pick_links(text, "https://www.x.example/"),
                         ["https://www.x.example/fees", "https://www.x.example/contact"])
        self.assertTrue(curate.skipped("www.psychologytoday.com".removeprefix("www.")))
        self.assertTrue(curate.skipped("care.zocdoc.com"))
        self.assertFalse(curate.skipped("getcare.hackensackmeridianhealth.org"))

    def test_quality_shrinks_small_counts(self):
        few = curate.quality([{"rating": 5.0, "count": 3, "source": "google"}])
        many = curate.quality([{"rating": 4.8, "count": 300, "source": "google"}])
        self.assertLess(few["avg"], many["avg"])
        self.assertIsNone(curate.quality([]))

    def test_parse_json_and_wrappers(self):
        self.assertEqual(llm.parse_json('Sure:\n```json\n{"a": [1]}\n```'), {"a": [1]})
        self.assertEqual(web.clean(wrap("hello")), "hello")


if __name__ == "__main__":
    unittest.main()

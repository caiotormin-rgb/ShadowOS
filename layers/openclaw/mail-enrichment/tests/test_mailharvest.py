"""Bulk harvester: the properties that only matter at scale.

A 23-message pilot can be re-run by hand when it fails. A 3,000-message
harvest cannot, so the things tested here are the ones whose absence turns a
30-minute run into a lost afternoon: resumability, backoff that recovers, and
not spending an API call on a logo.
"""
from __future__ import annotations

import base64
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "mail-context"))

import mailharvest as mh
from mailctx.gmail import RateLimiter, TransportError


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def message(mid="m1", *, atts=(), body=b"hello world", subject="Subject"):
    parts = [{"mimeType": "text/plain", "body": {"data": b64(body), "size": len(body)}}]
    for i, (name, data, mime, inline) in enumerate(atts):
        body_obj = ({"data": b64(data), "size": len(data)} if inline
                    else {"attachmentId": f"att{i}", "size": len(data)})
        parts.append({"mimeType": mime, "filename": name, "body": body_obj})
    return {
        "id": mid, "threadId": f"t-{mid}", "labelIds": ["INBOX"],
        "internalDate": "1700000000000",
        "payload": {"mimeType": "multipart/mixed",
                    "headers": [{"name": "From", "value": "Sam <sam@school.org>"},
                                {"name": "Subject", "value": subject},
                                {"name": "Date", "value": "Mon, 3 Feb 2025 10:00:00 -0300"}],
                    "parts": parts}}


class FakeApi:
    """Records every call so a test can assert what was NOT requested."""

    def __init__(self, messages: dict, blobs: dict | None = None, fail=None):
        self.messages = messages
        self.blobs = blobs or {}
        self.fail = fail or {}
        self.message_calls: list[str] = []
        self.attachment_calls: list[tuple[str, str]] = []

    def message_full(self, mid):
        self.message_calls.append(mid)
        plan = self.fail.get(mid)
        if plan:
            cls = plan.pop(0) if plan else None
            if cls:
                raise TransportError(cls, 403)
        if mid not in self.messages:
            raise TransportError("not_found", 404)
        return self.messages[mid]

    def attachment(self, mid, aid):
        self.attachment_calls.append((mid, aid))
        return {"data": b64(self.blobs.get((mid, aid), b"PDFDATA"))}


class Tmp(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)

    def tearDown(self):
        self._t.cleanup()

    def harvest(self, cands, api, **kw):
        state = mh.HarvestState(self.root / "state.sqlite")
        return self.resume(cands, api, state, **kw), state

    def resume(self, cands, api, state, **kw):
        kw.setdefault("progress_every", 0)
        kw.setdefault("workers", 1)
        kw.setdefault("sleep", lambda s: None)
        return mh.run_harvest(cands, self.root / "out", api_for=lambda: api,
                              state=state, log=io.StringIO(), **kw)


def cand(mid, category="human"):
    return {"message_id": mid, "category": category, "subject": "S"}


# -- fetching -----------------------------------------------------------

class FetchTest(Tmp):
    def test_attachment_and_body_are_written_with_metadata(self):
        api = FakeApi({"m1": message(atts=[("contract.pdf", b"X" * 60_000,
                                            "application/pdf", False)])})
        res = mh.fetch_message(api, "m1", self.root / "d")
        self.assertEqual(res.status, "ok")
        self.assertEqual([a["filename"] for a in res.attachments], ["contract.pdf"])
        meta = json.loads((self.root / "d" / "meta.json").read_text())
        self.assertEqual(meta["thread_id"], "t-m1")
        self.assertEqual(meta["subject"], "Subject")
        self.assertEqual((self.root / "d" / "body.txt").read_bytes().strip(), b"hello world")

    def test_chrome_is_skipped_without_spending_an_api_call(self):
        """The whole reason to filter at fetch time rather than at import time:
        ~80% of attachments by count are logos, and each one is a request."""
        api = FakeApi({"m1": message(atts=[("logo.png", b"x" * 900, "image/png", False),
                                           ("scan.pdf", b"y" * 90_000,
                                            "application/pdf", False)])})
        res = mh.fetch_message(api, "m1", self.root / "d")
        self.assertEqual([a["filename"] for a in res.attachments], ["scan.pdf"])
        self.assertEqual([s["reason"] for s in res.skipped], ["chrome"])
        self.assertEqual(len(api.attachment_calls), 1)      # never asked for the logo

    def test_a_large_image_is_not_chrome(self):
        api = FakeApi({"m1": message(atts=[("scan.png", b"x" * 500_000,
                                            "image/png", False)])})
        res = mh.fetch_message(api, "m1", self.root / "d")
        self.assertEqual([a["filename"] for a in res.attachments], ["scan.png"])

    def test_oversize_attachment_is_skipped_and_recorded(self):
        api = FakeApi({"m1": message(atts=[("huge.pdf", b"x" * 5_000,
                                            "application/pdf", False)])})
        res = mh.fetch_message(api, "m1", self.root / "d",
                               opts=mh.FetchOptions(max_attachment_bytes=1000))
        self.assertEqual(res.attachments, [])
        self.assertEqual(res.skipped[0]["reason"], "oversize")
        self.assertEqual(api.attachment_calls, [])

    def test_duplicate_filenames_do_not_overwrite_each_other(self):
        """Two parts called scan.pdf is ordinary mail, and losing one silently
        is exactly the kind of failure a harvest never notices."""
        api = FakeApi({"m1": message(atts=[
            ("scan.pdf", b"a" * 60_000, "application/pdf", False),
            ("scan.pdf", b"b" * 60_000, "application/pdf", False)])},
            blobs={("m1", "att0"): b"a" * 60_000, ("m1", "att1"): b"b" * 60_000})
        res = mh.fetch_message(api, "m1", self.root / "d")
        self.assertEqual(sorted(a["filename"] for a in res.attachments),
                         ["scan-1.pdf", "scan.pdf"])
        self.assertEqual(len({a["sha256"] for a in res.attachments}), 2)

    def test_inline_data_needs_no_second_request(self):
        api = FakeApi({"m1": message(atts=[("in.pdf", b"z" * 60_000,
                                            "application/pdf", True)])})
        res = mh.fetch_message(api, "m1", self.root / "d")
        self.assertEqual(len(res.attachments), 1)
        self.assertEqual(api.attachment_calls, [])

    def test_html_body_is_not_written_by_default(self):
        msg = message()
        msg["payload"]["parts"].append(
            {"mimeType": "text/html", "body": {"data": b64(b"<b>hi</b>"), "size": 9}})
        api = FakeApi({"m1": msg})
        mh.fetch_message(api, "m1", self.root / "d")
        self.assertFalse((self.root / "d" / "body.html").exists())


# -- resumability -------------------------------------------------------

class ResumeTest(Tmp):
    def test_a_second_run_fetches_only_what_is_missing(self):
        api = FakeApi({"m1": message("m1"), "m2": message("m2")})
        cands = [cand("m1"), cand("m2")]
        s1, state = self.harvest(cands, api)
        self.assertEqual(s1["fetched_this_run"], 2)
        n_after_first = len(api.message_calls)

        # New process, same state DB and out dir.
        state2 = mh.HarvestState(self.root / "state.sqlite")
        s2 = self.resume(cands + [cand("m3")], api, state2)
        self.assertEqual(s2["attempted"], 1)
        self.assertEqual(api.message_calls[n_after_first:], ["m3"])

    def test_progress_survives_a_crash_mid_run(self):
        """The failure this guards: a run dies at 900 of 3,000 and the operator
        has to pay for all 3,000 again."""
        api = FakeApi({"m1": message("m1"), "m2": message("m2")})
        state = mh.HarvestState(self.root / "state.sqlite")
        res = mh.fetch_message(api, "m1", self.root / "out" / "human-m1")
        res.message_id = "m1"
        state.record(res)
        del state                                  # simulate the process dying

        state2 = mh.HarvestState(self.root / "state.sqlite")
        summary = self.resume([cand("m1"), cand("m2")], api, state2)
        self.assertEqual(summary["attempted"], 1)
        self.assertEqual(summary["cumulative"]["ok"], 2)

    def test_a_deleted_message_is_never_retried(self):
        """not_found is permanent: no number of runs brings it back."""
        api = FakeApi({})
        s1, _ = self.harvest([cand("gone")], api, max_attempts=2)
        self.assertEqual(s1["failed_this_run"], 1)
        calls = len(api.message_calls)
        state2 = mh.HarvestState(self.root / "state.sqlite")
        s2 = self.resume([cand("gone")], api, state2, max_attempts=2)
        self.assertEqual(s2["attempted"], 0)
        self.assertEqual(len(api.message_calls), calls)

    def test_a_retryable_failure_is_attempted_again_by_the_next_run(self):
        """Attempts and runs are separate budgets. A message that burned every
        in-run attempt on a network failure still deserves the next run, because
        between runs the world changes -- that is why a human relaunched it."""
        api = FakeApi({"m1": message("m1")}, fail={"m1": ["network"] * 6})
        s1, _ = self.harvest([cand("m1")], api, max_attempts=2, max_runs=3)
        self.assertEqual(s1["failed_this_run"], 1)
        state2 = mh.HarvestState(self.root / "state.sqlite")
        s2 = self.resume([cand("m1")], api, state2, max_attempts=2, max_runs=3)
        self.assertEqual(s2["attempted"], 1)

    def test_a_message_that_fails_every_run_is_eventually_given_up_on(self):
        api = FakeApi({"m1": message("m1")}, fail={"m1": ["network"] * 20})
        cands = [cand("m1")]
        state = mh.HarvestState(self.root / "state.sqlite")
        for _ in range(2):
            self.resume(cands, api, state, max_attempts=1, max_runs=2)
        self.assertEqual(self.resume(cands, api, state,
                                     max_attempts=1, max_runs=2)["attempted"], 0)

    def test_an_expired_token_aborts_instead_of_burning_the_backlog(self):
        """The failure this prevents is silent and total: a token that dies at
        message 900 marks the remaining 2,100 'failed', and the resume logic
        that was supposed to rescue them skips them forever instead."""
        api = FakeApi({f"m{i}": message(f"m{i}") for i in range(6)},
                      fail={f"m{i}": ["auth"] for i in range(6)})
        summary, state = self.harvest([cand(f"m{i}") for i in range(6)], api,
                                      workers=1)
        self.assertEqual(summary["status"], "aborted")
        self.assertEqual(summary["aborted_because"], "auth")
        self.assertEqual(state.totals()["n"], 0)   # nothing recorded, nothing lost

    def test_after_an_abort_a_later_run_still_fetches_everything(self):
        fail = {f"m{i}": ["auth"] for i in range(4)}
        api = FakeApi({f"m{i}": message(f"m{i}") for i in range(4)}, fail=fail)
        cands = [cand(f"m{i}") for i in range(4)]
        state = mh.HarvestState(self.root / "state.sqlite")
        self.resume(cands, api, state, workers=1)
        fail.clear()                               # operator re-authorized
        s2 = self.resume(cands, api, state, workers=1)
        self.assertEqual(s2["fetched_this_run"], 4)

    def test_manifest_has_one_row_per_message(self):
        api = FakeApi({"m1": message("m1"), "m2": message("m2")})
        self.harvest([cand("m1"), cand("m2")], api)
        rows = [json.loads(l) for l in
                (self.root / "out" / "manifest.jsonl").read_text().splitlines()]
        self.assertEqual({r["message_id"] for r in rows}, {"m1", "m2"})
        self.assertTrue(all(r["sender"] for r in rows))

    def test_manifest_can_omit_subjects(self):
        api = FakeApi({"m1": message("m1")})
        self.harvest([cand("m1")], api, subjects_in_manifest=False)
        rows = [json.loads(l) for l in
                (self.root / "out" / "manifest.jsonl").read_text().splitlines()]
        self.assertNotIn("subject", rows[0])


# -- rate limiting ------------------------------------------------------

class BackoffTest(unittest.TestCase):
    def test_gmails_403_rate_limit_is_retried_and_then_succeeds(self):
        """Gmail reports per-user rate limiting as 403, not 429. Treating it as
        a permission denial would abandon the run at the first burst."""
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] < 3:
                raise TransportError("rate_limited", 403)
            return mh.MessageResult("m1")

        slept: list[float] = []
        res = mh.with_retry(fn, max_attempts=5, sleep=slept.append, rand=lambda: 0.5)
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.attempts, 3)
        self.assertEqual(len(slept), 2)

    def test_backoff_is_exponential(self):
        def fn():
            raise TransportError("rate_limited", 403)
        slept: list[float] = []
        mh.with_retry(fn, max_attempts=4, base_delay=2.0, sleep=slept.append,
                      rand=lambda: 0.5)
        self.assertEqual(slept, [2.0, 4.0, 8.0])

    def test_backoff_is_jittered(self):
        """Without jitter, N workers that hit the same limit wake together and
        hit it again together."""
        def fn():
            raise TransportError("rate_limited", 403)
        a, b = [], []
        mh.with_retry(fn, max_attempts=2, sleep=a.append, rand=lambda: 0.0)
        mh.with_retry(fn, max_attempts=2, sleep=b.append, rand=lambda: 1.0)
        self.assertNotEqual(a, b)
        self.assertLess(a[0], b[0])

    def test_backoff_is_capped(self):
        def fn():
            raise TransportError("rate_limited", 403)
        slept: list[float] = []
        mh.with_retry(fn, max_attempts=8, base_delay=2.0, max_delay=10.0,
                      sleep=slept.append, rand=lambda: 0.5)
        self.assertTrue(all(s <= 10.0 for s in slept), slept)

    def test_auth_failure_is_not_retried(self):
        """A revoked token will not fix itself; retrying just burns the clock."""
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise TransportError("auth", 401)
        res = mh.with_retry(fn, max_attempts=5, sleep=lambda s: None)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(res.error_class, "auth")

    def test_not_found_is_not_retried(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise TransportError("not_found", 404)
        mh.with_retry(fn, max_attempts=5, sleep=lambda s: None)
        self.assertEqual(calls["n"], 1)

    def test_an_unexpected_exception_fails_the_message_not_the_run(self):
        def fn():
            raise ValueError("bad base64")
        res = mh.with_retry(fn, max_attempts=3, sleep=lambda s: None)
        self.assertEqual(res.status, "failed")
        self.assertEqual(res.error_class, "ValueError")

    def test_recover_narrows_the_interval_back_toward_the_floor(self):
        lim = RateLimiter(per_second=10.0)
        floor = lim.min_interval
        for _ in range(5):
            lim.back_off()
        widened = lim.min_interval
        self.assertGreater(widened, floor)
        for _ in range(50):
            mh.recover(lim)
        self.assertAlmostEqual(lim.min_interval, floor, places=6)

    def test_recover_never_goes_below_what_the_caller_asked_for(self):
        lim = RateLimiter(per_second=10.0)
        for _ in range(50):
            mh.recover(lim)
        self.assertAlmostEqual(lim.min_interval, 0.1, places=6)


# -- the fetch list -----------------------------------------------------

class CandidateTest(Tmp):
    def test_columns_are_read_by_header_name_not_position(self):
        p = self.root / "c.tsv"
        p.write_text("category\tmessage_id\tdate\tscore\tsubject\n"
                     "human\tm1\t2025-01-01\t9\tHello\n")
        rows = mh.read_candidates(p)
        self.assertEqual(rows[0]["message_id"], "m1")
        self.assertEqual(rows[0]["score"], "9")

    def test_a_headerless_pilot_file_still_reads(self):
        p = self.root / "c.tsv"
        p.write_text("tax\tm1\tstuff\ntax\tm2\tstuff\n")
        rows = mh.read_candidates(p)
        self.assertEqual([r["message_id"] for r in rows], ["m1", "m2"])

    def test_blank_lines_are_ignored(self):
        p = self.root / "c.tsv"
        p.write_text("category\tmessage_id\nhuman\tm1\n\nhuman\t\n")
        self.assertEqual(len(mh.read_candidates(p)), 1)

    def test_order_is_preserved_so_a_truncated_run_keeps_the_best(self):
        p = self.root / "c.tsv"
        p.write_text("category\tmessage_id\nhuman\tbest\nunknown\tworst\n")
        self.assertEqual([r["message_id"] for r in mh.read_candidates(p)],
                         ["best", "worst"])


class ConcurrencyTest(Tmp):
    def test_workers_share_one_rate_limiter(self):
        """Raising --workers must raise parallelism, never the request rate."""
        lim = RateLimiter(per_second=1000.0)

        class FakeTokens:
            def bearer(self): return "x"

        factory = mh._api_factory(lim, opener=lambda *a, **k: (200, b"{}"),
                                  tokens=FakeTokens())
        self.assertIs(factory().limiter, lim)
        self.assertIs(factory().limiter, lim)

    def test_a_pool_run_records_every_message_exactly_once(self):
        msgs = {f"m{i}": message(f"m{i}") for i in range(12)}
        api = FakeApi(msgs)
        summary, state = self.harvest([cand(m) for m in msgs], api, workers=4)
        self.assertEqual(summary["fetched_this_run"], 12)
        self.assertEqual(state.totals()["n"], 12)


class NoSendPathTest(unittest.TestCase):
    """The harvest layer inherits the ceiling; assert it here too, because this
    is the code that holds a live token and writes bytes to disk."""

    FORBIDDEN = [re.compile(r"messages/send"), re.compile(r"drafts/[^\"']*send"),
                 re.compile(r"gmail\.send"),
                 re.compile(r"https://www\.googleapis\.com/auth/gmail\.(modify|full|send)"),
                 re.compile(r"\bmail\.google\.com\b")]

    def test_no_send_capable_code_in_the_enrichment_layer(self):
        offenders = []
        for path in sorted(ROOT.glob("*.py")):
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if line.strip().startswith("#"):
                    continue
                for rx in self.FORBIDDEN:
                    if rx.search(line):
                        offenders.append(f"{path.name}:{n}: {line.strip()}")
        self.assertEqual(offenders, [])

    def test_the_harvester_only_uses_the_read_only_transport(self):
        src = (ROOT / "mailharvest.py").read_text()
        self.assertIn("GmailReadOnly", src)
        self.assertNotIn("urllib.request.Request", src)


if __name__ == "__main__":
    unittest.main()

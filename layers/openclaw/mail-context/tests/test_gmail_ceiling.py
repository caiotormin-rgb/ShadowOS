"""Can the transport be talked into a write? It must not be possible."""
import json
import time
import unittest

from mailctx.auth import SCOPES, Token
from mailctx.gmail import ForbiddenEndpoint, GmailReadOnly, TransportError


class FakeTokens:
    def bearer(self):
        return "ya29.FAKE"


class Recorder:
    """Stands in for the network and records every request attempted."""

    def __init__(self, payload=None, raise_http=None):
        self.requests = []
        self.payload = payload if payload is not None else {}
        self.raise_http = raise_http

    def __call__(self, url, headers, timeout):
        self.requests.append((url, headers))
        if self.raise_http:
            raise self.raise_http
        return 200, json.dumps(self.payload).encode()


class CeilingTest(unittest.TestCase):
    def api(self, payload=None, raise_http=None):
        self.rec = Recorder(payload, raise_http)
        return GmailReadOnly(FakeTokens(), opener=self.rec)

    # -- the refusals ----------------------------------------------------
    def test_send_paths_are_refused(self):
        api = self.api()
        for path in ("/users/me/messages/send",
                     "/users/me/drafts/send",
                     "/users/me/drafts",
                     "/users/me/messages/abc/modify",
                     "/users/me/messages/abc/trash",
                     "/users/me/messages/batchDelete",
                     "/users/me/settings/forwarding",
                     "/users/me/watch"):
            with self.subTest(path=path):
                with self.assertRaises(ForbiddenEndpoint):
                    api._get(path)
        self.assertEqual(self.rec.requests, [], "no refused path may reach the network")

    def test_unknown_paths_are_refused_even_when_harmless(self):
        """The allowlist is a whitelist, not a blacklist."""
        api = self.api()
        for path in ("/users/me/messages/abc/attachments/xyz/extra",
                     "/users/me/", "/users/other/profile", "/../users/me/profile"):
            with self.subTest(path=path):
                with self.assertRaises(ForbiddenEndpoint):
                    api._get(path)

    def test_path_traversal_cannot_escape_the_allowlist(self):
        api = self.api()
        with self.assertRaises(ForbiddenEndpoint):
            api._get("/users/me/messages/..%2F..%2Fsend")

    def test_message_id_shaped_like_a_subpath_is_refused(self):
        """A hostile message id must not smuggle in a second path segment."""
        api = self.api()
        with self.assertRaises(ForbiddenEndpoint):
            api.message_metadata("abc/send")

    def test_every_http_request_in_the_module_is_a_get(self):
        """Structural, not textual: inspect each Request() call in the AST.

        An earlier version of this test counted the string `method="GET"`, which
        also matched the module docstring. Counting prose is not a safety check.
        """
        import ast, inspect
        from mailctx import gmail
        tree = ast.parse(inspect.getsource(gmail))
        requests = [n for n in ast.walk(tree)
                    if isinstance(n, ast.Call)
                    and ast.unparse(n.func).endswith("Request")]
        self.assertTrue(requests, "expected at least one urllib Request call")
        for call in requests:
            methods = [kw.value for kw in call.keywords if kw.arg == "method"]
            self.assertEqual(len(methods), 1, "Request() without an explicit method")
            self.assertIsInstance(methods[0], ast.Constant)
            self.assertEqual(methods[0].value, "GET",
                             f"non-GET request in transport: {ast.unparse(call)[:60]}")

    # -- the permitted surface still works -------------------------------
    def test_allowed_reads_reach_the_network(self):
        api = self.api({"emailAddress": "x@y.invalid"})
        api.profile()
        self.assertEqual(len(self.rec.requests), 1)
        self.assertTrue(self.rec.requests[0][0].endswith("/users/me/profile"))

    def test_metadata_format_is_always_requested(self):
        """format=metadata is what stops Gmail from ever sending a body."""
        api = self.api({"id": "m1"})
        api.message_metadata("m1")
        url = self.rec.requests[0][0]
        self.assertIn("format=metadata", url)
        self.assertNotIn("format=full", url)
        self.assertNotIn("format=raw", url)

    def test_spam_and_trash_are_excluded_by_default(self):
        api = self.api({"messages": []})
        api.list_message_ids()
        self.assertIn("includeSpamTrash=false", self.rec.requests[0][0])

    def test_bearer_is_sent_as_a_header_never_in_the_url(self):
        api = self.api({"labels": []})
        api.labels()
        url, headers = self.rec.requests[0]
        self.assertNotIn("ya29", url)
        self.assertEqual(headers["Authorization"], "Bearer ya29.FAKE")

    def test_history_requests_only_the_four_change_types(self):
        api = self.api({"history": []})
        api.history(1000)
        url = self.rec.requests[0][0]
        for t in ("messageAdded", "messageDeleted", "labelAdded", "labelRemoved"):
            self.assertIn(t, url)

    # -- error mapping ---------------------------------------------------
    def test_http_errors_map_to_classes_without_bodies(self):
        import urllib.error
        cases = {401: "auth", 403: "forbidden", 404: "not_found",
                 429: "rate_limited", 500: "http_error"}
        for code, expected in cases.items():
            with self.subTest(code=code):
                err = urllib.error.HTTPError("u", code, "msg", {}, None)
                api = self.api(raise_http=err)
                with self.assertRaises(TransportError) as cm:
                    api.profile()
                self.assertEqual(cm.exception.error_class, expected)
                self.assertNotIn("msg", str(cm.exception))

    # -- rate limiting ---------------------------------------------------
    def _http_403(self, body):
        import io, json as _json, urllib.error
        return urllib.error.HTTPError("u", 403, "Forbidden", {},
                                      io.BytesIO(_json.dumps(body).encode()))

    def test_gmail_403_rate_limit_is_not_mistaken_for_permission_denied(self):
        """Gmail reports per-user rate limiting as 403, never 429.

        Treating it as fatal is what killed a live 64k load at 5,000 messages.
        """
        for reason in ("rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"):
            with self.subTest(reason=reason):
                api = self.api(raise_http=self._http_403(
                    {"error": {"errors": [{"reason": reason}], "code": 403}}))
                with self.assertRaises(TransportError) as cm:
                    api.profile()
                self.assertEqual(cm.exception.error_class, "rate_limited")
                self.assertEqual(api.rate_limit_hits, 1)

    def test_genuine_403_is_still_fatal(self):
        api = self.api(raise_http=self._http_403(
            {"error": {"errors": [{"reason": "insufficientPermissions"}], "code": 403}}))
        with self.assertRaises(TransportError) as cm:
            api.profile()
        self.assertEqual(cm.exception.error_class, "forbidden")
        self.assertEqual(api.rate_limit_hits, 0)

    def test_403_classification_never_leaks_the_body(self):
        api = self.api(raise_http=self._http_403(
            {"error": {"errors": [{"reason": "x", "message": "SENSITIVE-ECHO"}]}}))
        with self.assertRaises(TransportError) as cm:
            api.profile()
        self.assertNotIn("SENSITIVE-ECHO", str(cm.exception))

    def test_unparseable_403_body_is_forbidden_not_a_crash(self):
        import io, urllib.error
        api = self.api(raise_http=urllib.error.HTTPError(
            "u", 403, "Forbidden", {}, io.BytesIO(b"<html>nope")))
        with self.assertRaises(TransportError) as cm:
            api.profile()
        self.assertEqual(cm.exception.error_class, "forbidden")

    def test_rate_limiter_spaces_requests(self):
        from mailctx.gmail import RateLimiter
        slept = []
        lim = RateLimiter(per_second=10.0)
        for _ in range(4):
            lim.acquire(sleep=slept.append)
        self.assertAlmostEqual(lim.min_interval, 0.1, places=6)
        self.assertGreater(sum(slept), 0.0, "requests after the first must wait")

    def test_back_off_widens_the_interval_and_is_capped(self):
        from mailctx.gmail import RateLimiter
        lim = RateLimiter(per_second=35.0)
        before = lim.min_interval
        lim.back_off()
        self.assertGreater(lim.min_interval, before)
        for _ in range(50):
            lim.back_off()
        self.assertLessEqual(lim.min_interval, 1.0, "backoff must not stall forever")

    def test_paginate_walks_pages_then_stops(self):
        pages = [{"messages": [{"id": "a"}], "nextPageToken": "t1"},
                 {"messages": [{"id": "b"}]}]
        seen = []

        def fn(page_token=None):
            seen.append(page_token)
            return pages[len(seen) - 1]

        api = self.api()
        self.assertEqual([m["id"] for m in api.paginate(fn, "messages")], ["a", "b"])
        self.assertEqual(seen, [None, "t1"])


if __name__ == "__main__":
    unittest.main()

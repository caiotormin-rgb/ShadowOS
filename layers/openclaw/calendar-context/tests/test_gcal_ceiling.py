"""Can the transport be talked into a write? It must not be possible."""
import json
import unittest

from calctx.gcal import (BASE, CalendarReadOnly, ForbiddenEndpoint, ForbiddenMethod,
                         IncompatibleParameters, TransportError)
from tests.fixtures import CAL_ID, NOW, SOON


class FakeTokens:
    def bearer(self):
        return "ya29.FAKE"


class Recorder:
    """Stands in for the network and records every request attempted."""

    def __init__(self, payload=None, raise_http=None):
        self.requests = []
        self.payload = payload if payload is not None else {}
        self.raise_http = raise_http

    def __call__(self, method, url, headers, timeout):
        self.requests.append((method, url, headers))
        if self.raise_http:
            raise self.raise_http
        return 200, json.dumps(self.payload).encode()


class CeilingTest(unittest.TestCase):
    def api(self, payload=None, raise_http=None):
        self.rec = Recorder(payload, raise_http)
        return CalendarReadOnly(FakeTokens(), opener=self.rec)

    # -- the refusals ----------------------------------------------------
    def test_write_paths_are_refused(self):
        api = self.api()
        for path in ("/calendars/primary/events/import",
                     "/calendars/primary/events/abc/move",
                     "/calendars/primary/events/quickAdd",
                     "/calendars/primary/clear",
                     "/calendars/primary/acl",
                     "/calendars/primary/events/watch",
                     "/channels/stop",
                     "/freeBusy",
                     "/users/me/settings"):
            with self.subTest(path=path):
                with self.assertRaises(ForbiddenEndpoint):
                    api._get(path)
        self.assertEqual(self.rec.requests, [], "no refused path may reach the network")

    def test_unknown_paths_are_refused_even_when_harmless(self):
        """The allowlist is a whitelist, not a blacklist."""
        api = self.api()
        for path in ("/calendars/primary", "/calendars/primary/events/abc",
                     "/colors", "/users/me/", "/../users/me/calendarList",
                     "/calendars/primary/events/abc/instances/def"):
            with self.subTest(path=path):
                with self.assertRaises(ForbiddenEndpoint):
                    api._get(path)

    def test_a_hostile_calendar_id_cannot_become_a_path_segment(self):
        """Encoding alone would be enough to keep it one segment, but a real
        calendar id never contains a slash, so an encoded one is refused
        outright rather than quietly forwarded to Google."""
        api = self.api({"items": []})
        with self.assertRaises(ForbiddenEndpoint):
            api.list_events("evil/clear")
        self.assertEqual(self.rec.requests, [])

    def test_a_normal_calendar_id_is_percent_encoded(self):
        api = self.api({"items": []})
        api.list_events(CAL_ID)
        url = self.rec.requests[0][1]
        self.assertIn("primary%40example.invalid/events", url)

    def test_an_encoded_slash_in_a_raw_path_is_refused(self):
        api = self.api()
        with self.assertRaises(ForbiddenEndpoint):
            api._get("/calendars/x%2Fclear/events")

    def test_no_verb_other_than_get_can_be_requested(self):
        api = self.api()
        for verb in ("POST", "PUT", "PATCH", "DELETE", "get", ""):
            with self.subTest(verb=verb):
                with self.assertRaises(ForbiddenMethod):
                    api._request(verb, "/users/me/calendarList")

    def test_every_http_request_in_the_module_is_a_get(self):
        """Structural, not textual: inspect each Request() call in the AST.

        Counting the string `method="GET"` would also match this docstring, and
        counting prose is not a safety check.
        """
        import ast, inspect
        from calctx import gcal
        tree = ast.parse(inspect.getsource(gcal))
        requests = [n for n in ast.walk(tree)
                    if isinstance(n, ast.Call) and ast.unparse(n.func).endswith("Request")]
        self.assertTrue(requests, "expected at least one urllib Request call")
        for call in requests:
            methods = [kw.value for kw in call.keywords if kw.arg == "method"]
            self.assertEqual(len(methods), 1, "Request() without an explicit method")
            self.assertIsInstance(methods[0], ast.Constant)
            self.assertEqual(methods[0].value, "GET",
                             f"non-GET request in transport: {ast.unparse(call)[:60]}")

    # -- the parameter combination Google forbids ------------------------
    def test_sync_token_with_a_time_window_is_refused_before_the_call(self):
        """events.list forbids the combination; discovering that at runtime
        would mean an index that looks healthy and is quietly wrong."""
        api = self.api({"items": []})
        with self.assertRaises(IncompatibleParameters):
            api.list_events(CAL_ID, sync_token="tok", time_min="2026-01-01T00:00:00Z")
        with self.assertRaises(IncompatibleParameters):
            api.list_events(CAL_ID, sync_token="tok", time_max="2026-01-01T00:00:00Z")
        self.assertEqual(self.rec.requests, [])

    # -- the permitted surface still works -------------------------------
    def test_allowed_reads_reach_the_network(self):
        api = self.api({"items": []})
        api.calendar_list()
        self.assertEqual(len(self.rec.requests), 1)
        method, url, _ = self.rec.requests[0]
        self.assertEqual(method, "GET")
        self.assertTrue(url.startswith(BASE + "/users/me/calendarList"))

    def test_series_layer_never_asks_google_to_expand(self):
        """singleEvents=false is what keeps the RRULE auditable locally."""
        api = self.api({"items": []})
        api.list_events(CAL_ID, time_min="2026-01-01T00:00:00Z", time_max="2027-01-01T00:00:00Z")
        url = self.rec.requests[0][1]
        self.assertIn("singleEvents=false", url)
        self.assertNotIn("singleEvents=true", url)

    def test_deleted_events_are_requested_so_cancellations_are_visible(self):
        api = self.api({"items": []})
        api.list_events(CAL_ID, sync_token="tok")
        self.assertIn("showDeleted=true", self.rec.requests[0][1])

    def test_instances_are_bounded_and_include_cancellations(self):
        api = self.api({"items": []})
        api.instances(CAL_ID, "abc", time_min="2026-01-01T00:00:00Z",
                      time_max="2027-01-01T00:00:00Z")
        url = self.rec.requests[0][1]
        self.assertIn("timeMin=", url)
        self.assertIn("timeMax=", url)
        self.assertIn("showDeleted=true", url)

    def test_bearer_is_sent_as_a_header_never_in_the_url(self):
        api = self.api({"items": []})
        api.calendar_list()
        _method, url, headers = self.rec.requests[0]
        self.assertNotIn("ya29", url)
        self.assertEqual(headers["Authorization"], "Bearer ya29.FAKE")

    # -- error mapping ---------------------------------------------------
    def test_http_errors_map_to_classes_without_bodies(self):
        import urllib.error
        cases = {401: "auth", 403: "forbidden_or_quota", 404: "not_found",
                 410: "gone", 429: "rate_limited", 500: "http_error"}
        for code, expected in cases.items():
            with self.subTest(code=code):
                err = urllib.error.HTTPError("u", code, "secret-body", {}, None)
                api = self.api(raise_http=err)
                with self.assertRaises(TransportError) as cm:
                    api.calendar_list()
                self.assertEqual(cm.exception.error_class, expected)
                self.assertNotIn("secret-body", str(cm.exception))

    def test_410_is_its_own_class_because_it_is_recoverable(self):
        """An expired syncToken is expected operation, not a failure."""
        import urllib.error
        api = self.api(raise_http=urllib.error.HTTPError("u", 410, "gone", {}, None))
        with self.assertRaises(TransportError) as cm:
            api.list_events(CAL_ID, sync_token="stale")
        self.assertEqual(cm.exception.error_class, "gone")
        self.assertNotEqual(cm.exception.error_class, "http_error")

    def test_paginate_walks_pages_then_stops(self):
        pages = [{"items": [{"id": "a"}], "nextPageToken": "t1"}, {"items": [{"id": "b"}]}]
        seen = []

        def fn(page_token=None):
            seen.append(page_token)
            return pages[len(seen) - 1]

        api = self.api()
        self.assertEqual([i["id"] for i in api.paginate(fn, "items")], ["a", "b"])
        self.assertEqual(seen, [None, "t1"])


if __name__ == "__main__":
    unittest.main()

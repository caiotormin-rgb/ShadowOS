"""The safety property the whole V1 rests on.

Mail-context has a narrow write path and had to prove it could not send.
Calendar-context has no write path at all -- not for events, not for RSVPs, not
for reminders -- and this is where that claim stops being prose.

Four independent assertions, because a single one is a single point of failure:

1. no write-capable Calendar endpoint or HTTP verb appears in the source;
2. every OAuth scope string in the package is a Calendar *readonly* scope;
3. no public method on the API or query surface is named for a write verb;
4. behaviourally, a full sync driven through a recording transport issues GET
   and nothing else.

`calctx/store.py` is deliberately in scope for (1) and (2) but not (3): it holds
INSERT and DELETE against *our own* SQLite index, which is the entire point of a
local read model. The distinction that matters is whether anything can write to
Google, and none of these four permits that.
"""
from __future__ import annotations

import ast
import inspect
import json
import re
import unittest
from pathlib import Path

from calctx import gcal, query
from calctx.gcal import CalendarReadOnly
from calctx.store import Store, connect, migrate
from calctx.sync import Syncer
from tests.fixtures import (CAL_ID, SOON, memory_conn, raw_calendar, raw_instance,
                            raw_timed)

PKG = Path(__file__).resolve().parent.parent / "calctx"

# Patterns that match a write being *used*, not a write being *denied*. The
# ceiling in gcal.py necessarily names the mutating segments it refuses; a test
# that also matched that denylist would fail on the very code protecting us.
FORBIDDEN = [
    re.compile(r"/calendars/[^\"']*/(import|move|quickAdd|clear|acl)"),
    re.compile(r"/events/(import|move|quickAdd|watch)"),
    re.compile(r"method\s*=\s*[\"'](POST|PUT|PATCH|DELETE)[\"']"),
    re.compile(r"[\"'](POST|PUT|PATCH|DELETE)[\"']"),
    re.compile(r"sendUpdates\s*[=:]"),
    re.compile(r"\.(insert|quickAdd|import_event)\s*\("),
]

ALLOWED_SCOPES = {
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/calendar.events.readonly",
    # The plan's only approved fallback if the granular scope is unavailable.
    "https://www.googleapis.com/auth/calendar.readonly",
}

WRITE_VERB = re.compile(
    r"(create|insert|update|patch|delete|remove|move|import|quickadd|respond|rsvp|send|write)",
    re.IGNORECASE)


class FakeTokens:
    def bearer(self):
        return "ya29.FAKE"


class RecordingTransport:
    """Stands in for the network and records the verb of every request.

    The verb is threaded through the opener signature on purpose: a test that
    only inspected the source for `method="GET"` would be trusting the thing it
    is meant to check.
    """

    def __init__(self):
        self.requests: list[tuple[str, str]] = []

    def __call__(self, method, url, headers, timeout):
        self.requests.append((method, url))
        if "/calendarList" in url:
            body = {"items": [raw_calendar()]}
        elif url.split("?")[0].endswith("/instances"):
            body = {"items": [raw_instance("s1_0", start=SOON)]}
        else:
            body = {"items": [raw_timed("e1"),
                              raw_timed("s1", recurrence=["RRULE:FREQ=WEEKLY"])],
                    "nextSyncToken": "tok-1"}
        return 200, json.dumps(body).encode()


class NoWritePathTest(unittest.TestCase):
    def _sources(self):
        return sorted(PKG.rglob("*.py"))

    # -- 1. the source ---------------------------------------------------
    def test_package_contains_no_write_endpoint_or_verb(self):
        offenders = []
        for path in self._sources():
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue  # prose about not writing is fine; calls are not
                for pattern in FORBIDDEN:
                    if pattern.search(line):
                        offenders.append(f"{path.name}:{lineno}: {stripped}")
        self.assertEqual(offenders, [], "write-capable code found:\n" + "\n".join(offenders))

    def test_only_one_module_can_open_a_socket(self):
        """A second HTTP client is how a write path gets in through the back door."""
        openers = [p.name for p in self._sources() if "urllib.request" in p.read_text()]
        self.assertEqual(openers, ["gcal.py"])

    # -- 2. the grant ----------------------------------------------------
    def test_declared_scopes_are_readonly(self):
        scope_re = re.compile(r"https://www\.googleapis\.com/auth/[\w.]+")
        found = set()
        for path in self._sources():
            found.update(scope_re.findall(path.read_text()))
        self.assertTrue(found, "expected the package to declare its scopes")
        self.assertTrue(found <= ALLOWED_SCOPES,
                        f"non-readonly or unexpected scopes: {sorted(found - ALLOWED_SCOPES)}")
        for scope in found:
            self.assertTrue(scope.endswith(".readonly"), scope)

    # -- 3. the tool surface ---------------------------------------------
    def test_no_public_method_is_named_for_a_write(self):
        for module, class_name in ((query, "CalendarContext"), (gcal, "CalendarReadOnly")):
            tree = ast.parse(inspect.getsource(module))
            classes = [n for n in ast.walk(tree)
                       if isinstance(n, ast.ClassDef) and n.name == class_name]
            self.assertEqual(len(classes), 1, f"{class_name} not found")
            names = [f.name for f in classes[0].body
                     if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and not f.name.startswith("_")]
            self.assertTrue(names, f"{class_name} has no public methods to check")
            for name in names:
                with self.subTest(cls=class_name, method=name):
                    self.assertIsNone(WRITE_VERB.search(name),
                                      f"{class_name}.{name} is named for a write")

    def test_the_query_contract_is_exactly_the_six_approved_methods(self):
        public = {n for n in dir(query.CalendarContext)
                  if not n.startswith("_") and callable(getattr(query.CalendarContext, n))}
        self.assertEqual(
            public,
            {"agenda", "search", "event", "conflicts", "match_appointment", "status", "close"},
            "the agent-facing contract grew or shrank; that is a deliberate decision")

    # -- 4. behaviour ----------------------------------------------------
    def test_a_full_sync_issues_get_and_nothing_else(self):
        conn = memory_conn()
        migrate(conn)
        rec = RecordingTransport()
        api = CalendarReadOnly(FakeTokens(), opener=rec)
        res = Syncer(Store(conn), api, concurrency=1).sync()
        self.assertEqual(res.status, "ok")
        self.assertTrue(rec.requests, "the sync must actually have called Google")
        self.assertEqual({m for m, _ in rec.requests}, {"GET"})
        for _method, url in rec.requests:
            self.assertTrue(url.startswith("https://www.googleapis.com/calendar/v3/"), url)

    def test_the_transport_refuses_a_non_get_even_when_asked_directly(self):
        rec = RecordingTransport()
        api = CalendarReadOnly(FakeTokens(), opener=rec)
        for verb in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(verb=verb):
                with self.assertRaises(gcal.ForbiddenMethod):
                    api._request(verb, "/users/me/calendarList")
        self.assertEqual(rec.requests, [], "no refused verb may reach the network")

    def test_the_default_opener_also_refuses_a_non_get(self):
        """The guard lives in the socket-opening function too, not only above it."""
        with self.assertRaises(gcal.ForbiddenMethod):
            gcal._default_opener("POST", "https://example.invalid", {}, 1)


if __name__ == "__main__":
    unittest.main()

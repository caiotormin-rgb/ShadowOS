"""Scope and token-file policy.

The OAuth broker itself is mail-context's and is tested there. What is tested
here is the part that must NOT be shared between layers: which scopes this
layer asks for, and where its token lives. One Desktop client, one consent, but
a separate grant per layer -- a shared client is not a shared capability.
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from calctx import auth
from calctx.auth import SCOPES, Token, TokenStore, scopes_are_readonly

CLIENT = {"installed": {"client_id": "123-abc.apps.googleusercontent.com",
                        "client_secret": "GOCSPX-" + "x" * 28,
                        "project_id": "proj", "redirect_uris": ["http://localhost"]}}


class ScopeTest(unittest.TestCase):
    def test_scopes_are_exactly_the_two_granular_readonly_ones(self):
        self.assertEqual(SCOPES, (
            "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
            "https://www.googleapis.com/auth/calendar.events.readonly",
        ))

    def test_the_calendarlist_scope_is_present_because_events_alone_cannot_list(self):
        """Without it the indexer could only read a hardcoded `primary` and
        could not discover a calendar's IANA timezone."""
        self.assertIn("calendar.calendarlist.readonly", " ".join(SCOPES))

    def test_the_approved_fallback_is_still_write_free(self):
        self.assertEqual(auth.FALLBACK_SCOPES,
                         ("https://www.googleapis.com/auth/calendar.readonly",))
        self.assertTrue(scopes_are_readonly(auth.FALLBACK_SCOPES))

    def test_a_write_capable_grant_is_rejected(self):
        for bad in ("https://www.googleapis.com/auth/calendar",
                    "https://www.googleapis.com/auth/calendar.events",
                    "https://www.googleapis.com/auth/calendar.events.owned",
                    "https://www.googleapis.com/auth/calendar.acls"):
            with self.subTest(scope=bad):
                self.assertFalse(scopes_are_readonly((bad,)))

    def test_a_readonly_scope_from_another_product_is_still_rejected(self):
        self.assertFalse(scopes_are_readonly(
            ("https://www.googleapis.com/auth/gmail.readonly",)))

    def test_an_empty_grant_is_not_readonly(self):
        self.assertFalse(scopes_are_readonly(()))

    def test_a_mixed_grant_is_rejected_whole(self):
        self.assertFalse(scopes_are_readonly(
            (SCOPES[0], "https://www.googleapis.com/auth/calendar")))


class TokenSeparationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_the_token_file_is_this_layers_own(self):
        self.assertEqual(auth.TOKEN_PATH.name, "calendar-token.json")
        self.assertNotEqual(auth.TOKEN_PATH.parent, Path.home() / ".config" / "mail-context")

    def test_the_desktop_client_is_shared_but_the_token_is_not(self):
        """The operator consents once; revoking one layer must not be confused
        with revoking the other."""
        self.assertEqual(auth.CLIENT_PATH.name, "client_secret.json")
        self.assertEqual(auth.CLIENT_PATH.parent.name, "mail-context")
        self.assertEqual(auth.TOKEN_PATH.parent.name, "calendar-context")

    def test_a_saved_token_is_private(self):
        path = self.dir / "calendar-token.json"
        TokenStore(path).save(Token("a", "r", int(time.time()) + 3600, SCOPES))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_the_stored_grant_round_trips(self):
        path = self.dir / "calendar-token.json"
        store = TokenStore(path)
        store.save(Token("a", "r", 1234, SCOPES))
        self.assertEqual(store.load().scopes, SCOPES)

    def test_a_token_never_renders_its_value(self):
        t = Token("ya29.SUPERSECRET", "1//REFRESHSECRET", int(time.time()) + 60, SCOPES)
        for rendered in (repr(t), str(t), f"{t}"):
            self.assertNotIn("SUPERSECRET", rendered)
            self.assertNotIn("REFRESHSECRET", rendered)

    def test_client_config_is_loaded_through_the_shared_broker(self):
        path = self.dir / "client_secret.json"
        path.write_text(json.dumps(CLIENT))
        cfg = auth.ClientConfig.load(path)
        self.assertEqual(cfg.project_id, "proj")
        self.assertNotIn(CLIENT["installed"]["client_secret"], repr(cfg))


if __name__ == "__main__":
    unittest.main()

"""The two things this layer borrows from mail-context, and the one it does not.

Borrowed: the encryption preflight gate and the OAuth broker, imported rather
than reimplemented. Not borrowed: the token. One Desktop client means the
operator consents once; it does not mean one capability.
"""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path

import mailctx.auth
import mailctx.preflight

from drivectx import auth, syncrun

CLIENT = {"installed": {"client_id": "123-abc.apps.googleusercontent.com",
                        "client_secret": "GOCSPX-" + "x" * 28,
                        "project_id": "proj", "redirect_uris": ["http://localhost"]}}


class BrokerReuseTest(unittest.TestCase):
    def test_the_broker_is_imported_not_copied(self):
        """A second implementation of the same flow is a second set of bugs."""
        self.assertIs(auth.AccessTokenProvider, mailctx.auth.AccessTokenProvider)
        self.assertIs(auth.TokenStore, mailctx.auth.TokenStore)
        self.assertIs(auth.authorize_interactive, mailctx.auth.authorize_interactive)

    def test_the_preflight_gate_is_imported_not_copied(self):
        self.assertIs(syncrun.preflight, mailctx.preflight)

    def test_the_scope_is_metadata_only(self):
        self.assertEqual(auth.SCOPES,
                         ("https://www.googleapis.com/auth/drive.metadata.readonly",))

    def test_the_oauth_client_is_shared_and_the_token_is_not(self):
        self.assertEqual(auth.CLIENT_PATH, mailctx.auth.CLIENT_PATH,
                         "one Desktop client: the operator consents once")
        self.assertNotEqual(auth.TOKEN_PATH, mailctx.auth.TOKEN_PATH,
                            "a shared client must not mean a shared token")
        self.assertEqual(auth.TOKEN_PATH.name, "drive-token.json")

    def test_a_drive_token_roundtrips_privately_without_touching_the_mail_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "drive-token.json"
            store = auth.token_store(path)
            store.save(auth.Token("acc", "ref", int(time.time()) + 3600, auth.SCOPES))
            loaded = store.load()
            self.assertEqual(loaded.scopes, auth.SCOPES)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("acc", repr(loaded), "the token must not survive a repr")

    def test_the_provider_is_bound_to_the_drive_token_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            client_path = Path(tmp) / "client_secret.json"
            client_path.write_text(json.dumps(CLIENT))
            store = auth.token_store(Path(tmp) / "drive-token.json")
            provider = auth.provider(auth.ClientConfig.load(client_path), store)
            self.assertFalse(provider.status()["authorized"])
            self.assertNotIn("secret", repr(provider).lower().replace("<redacted>", ""))


class PreflightGateTest(unittest.TestCase):
    """The gate is a gate, not a warning to click through."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "state" / "drive-context.sqlite"
        self.real = mailctx.preflight.require_for_initial_sync
        self.addCleanup(setattr, mailctx.preflight, "require_for_initial_sync", self.real)

    def run_cli(self, argv):
        """The CLI reports to a human on stdout/stderr; the test cares about the
        exit code and the filesystem, so its output is captured rather than
        printed into the test run."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = syncrun.main(argv)
        return rc, out.getvalue() + err.getvalue()

    def test_a_failing_gate_stops_the_run_before_anything_is_stored(self):
        def refuse(db_path, **kw):
            raise mailctx.preflight.PreflightError(mailctx.preflight.Preflight((
                mailctx.preflight.Check("encryption_at_rest", False, "plaintext disk"),)))

        mailctx.preflight.require_for_initial_sync = refuse
        rc, output = self.run_cli(["--db", str(self.db)])
        self.assertEqual(rc, 3)
        self.assertIn("encryption_at_rest", output, "the refusal must say why")
        self.assertFalse(self.db.exists(), "no index may be created behind a failed gate")

    def test_the_gate_runs_against_the_drive_state_path(self):
        seen = {}

        def spy(db_path, **kw):
            seen["path"] = Path(db_path)
            raise mailctx.preflight.PreflightError(mailctx.preflight.Preflight((
                mailctx.preflight.Check("encryption_at_rest", False, "plaintext disk"),)))

        mailctx.preflight.require_for_initial_sync = spy
        self.run_cli(["--db", str(self.db)])
        self.assertEqual(seen["path"], self.db)

    def test_the_default_database_lives_under_the_private_state_directory(self):
        self.assertEqual(syncrun.DB_PATH.parent.name, "drive-context")
        self.assertIn(".local/state", str(syncrun.DB_PATH))

    def test_status_on_a_missing_index_does_not_need_the_gate(self):
        """Asking 'is there an index' must not require an encrypted disk."""
        def explode(db_path, **kw):
            raise AssertionError("status must not run the gate")

        mailctx.preflight.require_for_initial_sync = explode
        self.assertEqual(self.run_cli(["--db", str(self.db), "--status"])[0], 0)


if __name__ == "__main__":
    unittest.main()

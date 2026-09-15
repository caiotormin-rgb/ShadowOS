import json
import tempfile
import time
import unittest
from pathlib import Path

from mailctx.auth import (SCOPES, AccessTokenProvider, AuthError, ClientConfig, Token, TokenStore)

CLIENT = {"installed": {"client_id": "123-abc.apps.googleusercontent.com",
                        "client_secret": "GOCSPX-" + "x" * 28,
                        "project_id": "proj", "redirect_uris": ["http://localhost"]}}


class AuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.client_path = self.dir / "client_secret.json"
        self.client_path.write_text(json.dumps(CLIENT))
        self.token_path = self.dir / "token.json"

    def client(self):
        return ClientConfig.load(self.client_path)

    # -- credential material must not leak into strings ------------------
    def test_client_config_repr_hides_secret(self):
        r = repr(self.client())
        self.assertNotIn(CLIENT["installed"]["client_secret"], r)
        self.assertIn("redacted", r)

    def test_token_repr_and_str_hide_value(self):
        t = Token("ya29.SUPERSECRET", "1//REFRESHSECRET", int(time.time()) + 3600, SCOPES)
        for rendered in (repr(t), str(t), f"{t}", "{}".format(t)):
            self.assertNotIn("SUPERSECRET", rendered)
            self.assertNotIn("REFRESHSECRET", rendered)
        self.assertNotIn("SUPERSECRET", repr(AccessTokenProvider(self.client(), TokenStore(self.token_path))))

    def test_token_is_not_in_the_provider_dict(self):
        """A naive vars()/__dict__ dump must not spill the token."""
        t = Token("ya29.SUPERSECRET", "1//R", int(time.time()) + 3600, SCOPES)
        self.assertFalse(hasattr(t, "__dict__"), "Token uses __slots__ so vars() cannot dump it")

    # -- storage ---------------------------------------------------------
    def test_saved_token_file_is_private(self):
        store = TokenStore(self.token_path)
        store.save(Token("a", "r", int(time.time()) + 3600, SCOPES))
        self.assertEqual(self.token_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.token_path.parent.stat().st_mode & 0o777, 0o700)

    def test_save_leaves_no_readable_temp_file(self):
        store = TokenStore(self.token_path)
        store.save(Token("a", "r", int(time.time()) + 3600, SCOPES))
        self.assertEqual(list(self.dir.glob("*.tmp")), [])

    def test_roundtrip_and_clear(self):
        store = TokenStore(self.token_path)
        store.save(Token("acc", "ref", 1234, SCOPES))
        self.assertEqual(store.load().expires_at, 1234)
        self.assertTrue(store.clear())
        self.assertIsNone(store.load())
        self.assertFalse(store.clear())

    def test_corrupt_token_file_is_an_error_not_a_crash(self):
        self.token_path.write_text("{not json")
        with self.assertRaises(AuthError):
            TokenStore(self.token_path).load()

    # -- client config validation ---------------------------------------
    def test_missing_client_config(self):
        with self.assertRaises(AuthError):
            ClientConfig.load(self.dir / "nope.json")

    def test_web_client_is_rejected(self):
        p = self.dir / "web.json"
        p.write_text(json.dumps({"web": {"client_id": "x", "client_secret": "y"}}))
        with self.assertRaises(AuthError) as cm:
            ClientConfig.load(p)
        self.assertIn("Desktop", str(cm.exception))

    # -- refresh ---------------------------------------------------------
    def test_expired_token_is_refreshed_and_persisted(self):
        store = TokenStore(self.token_path)
        store.save(Token("old", "ref", int(time.time()) - 10, SCOPES))
        calls = []

        def fake_post(url, fields):
            calls.append(fields["grant_type"])
            return {"access_token": "new", "expires_in": 3600}

        p = AccessTokenProvider(self.client(), store, post_form=fake_post)
        self.assertEqual(p.bearer(), "new")
        self.assertEqual(calls, ["refresh_token"])
        self.assertEqual(store.load().expires_at > time.time(), True)

    def test_live_token_is_not_refreshed(self):
        store = TokenStore(self.token_path)
        store.save(Token("live", "ref", int(time.time()) + 3600, SCOPES))

        def boom(url, fields):
            raise AssertionError("must not refresh a live token")

        self.assertEqual(AccessTokenProvider(self.client(), store, post_form=boom).bearer(), "live")

    def test_refresh_keeps_the_refresh_token_when_google_omits_it(self):
        store = TokenStore(self.token_path)
        store.save(Token("old", "THEREFRESH", int(time.time()) - 10, SCOPES))
        p = AccessTokenProvider(self.client(), store,
                                post_form=lambda u, f: {"access_token": "new", "expires_in": 60})
        p.bearer()
        self.assertIn("THEREFRESH", store.path.read_text(),
                      "Google omits refresh_token on refresh; dropping it would break the timer")

    def test_status_is_safe_to_log(self):
        store = TokenStore(self.token_path)
        store.save(Token("ya29.SECRETVALUE", "1//SECRETREFRESH", int(time.time()) + 600, SCOPES))
        s = json.dumps(AccessTokenProvider(self.client(), store).status())
        self.assertNotIn("SECRETVALUE", s)
        self.assertNotIn("SECRETREFRESH", s)
        self.assertIn("expires_in", s)

    def test_status_before_authorization(self):
        s = AccessTokenProvider(self.client(), TokenStore(self.token_path)).status()
        self.assertFalse(s["authorized"])

    def test_scopes_are_exactly_what_phase_one_approved(self):
        self.assertEqual(SCOPES, ("https://www.googleapis.com/auth/gmail.readonly",))

    def test_broker_accepts_a_sibling_layers_scopes_and_token_file(self):
        """calendar-context and drive-context reuse this broker; a shared token
        must not mean a shared capability, so each keeps its own file."""
        other = self.dir / "calendar-token.json"
        store = TokenStore(other)
        store.save(Token("a", "r", int(time.time()) + 3600,
                         ("https://www.googleapis.com/auth/calendar.readonly",)))
        loaded = store.load()
        self.assertEqual(loaded.scopes, ("https://www.googleapis.com/auth/calendar.readonly",))
        self.assertEqual(other.stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.token_path.exists() is False, "sibling tokens stay separate")


if __name__ == "__main__":
    unittest.main()



class ManualAuthTest(unittest.TestCase):
    """Manual mode exists because an SSH forward run from the wrong host binds
    the callback port on the very host that needs it, and the resulting
    'Address already in use' gives no hint of the real cause."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "client_secret.json").write_text(json.dumps(CLIENT))
        self.client = ClientConfig.load(self.dir / "client_secret.json")
        self.store = TokenStore(self.dir / "token.json")

    def _run(self, reply, posted=None, port=0):
        """Drive manual mode. `reply` may be a string, or a callable given the
        text printed so far (so a test can echo back the real state)."""
        import builtins, contextlib, io
        import mailctx.auth as auth

        buf = io.StringIO()

        def fake_post(url, fields):
            return posted if posted is not None else {
                "access_token": "acc", "refresh_token": "ref", "expires_in": 3600,
                "scope": " ".join(SCOPES)}

        orig_post, orig_input = auth._post_form, builtins.input
        auth._post_form = fake_post
        builtins.input = lambda _p="": reply(buf.getvalue()) if callable(reply) else reply
        try:
            with contextlib.redirect_stdout(buf):
                token = auth.authorize_interactive(self.client, store=self.store,
                                                   open_browser=False, manual=True,
                                                   port=port)
            return token, buf.getvalue()
        finally:
            auth._post_form, builtins.input = orig_post, orig_input

    @staticmethod
    def _echo_state(printed):
        import re, urllib.parse
        url = re.search(r"https://accounts\.google\.com/\S+", printed).group(0)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        port = urllib.parse.urlparse(q["redirect_uri"][0]).port
        return f"http://localhost:{port}/?code=THECODE&state={q['state'][0]}"

    def test_works_even_when_the_callback_port_is_taken(self):
        """The exact failure that motivated this mode.

        Binds a real socket first, then authorizes against that same port. In
        listener mode this raises EADDRINUSE; manual mode never opens a socket,
        so it must succeed.
        """
        import socket
        squatter = socket.socket()
        squatter.bind(("127.0.0.1", 0))
        port = squatter.getsockname()[1]
        squatter.listen(1)
        self.addCleanup(squatter.close)

        with self.assertRaises(OSError, msg="precondition: the port really is taken"):
            import mailctx.auth as auth
            auth.HTTPServer(("127.0.0.1", port), auth._CallbackHandler)

        token, printed = self._run(self._echo_state, port=port)
        self.assertEqual(token.scopes, SCOPES)
        self.assertIn(f"localhost%3A{port}", printed)

    def test_round_trip_persists_a_usable_token(self):
        token, _ = self._run(self._echo_state)
        self.assertFalse(token.expired)
        self.assertEqual(self.store.load().scopes, SCOPES)
        self.assertEqual((self.dir / "token.json").stat().st_mode & 0o777, 0o600)

    def test_state_mismatch_is_rejected(self):
        with self.assertRaises(AuthError) as cm:
            self._run("http://localhost:8766/?code=X&state=NOTTHEONE")
        self.assertIn("state mismatch", str(cm.exception))

    def test_url_without_a_query_string_is_explained(self):
        with self.assertRaises(AuthError) as cm:
            self._run("http://localhost:8766/")
        self.assertIn("no query string", str(cm.exception))

    def test_empty_paste_is_explained(self):
        with self.assertRaises(AuthError) as cm:
            self._run("")
        self.assertIn("no redirect URL", str(cm.exception))

    def test_a_wider_grant_than_requested_is_refused(self):
        wide = {"access_token": "a", "refresh_token": "r", "expires_in": 3600,
                "scope": " ".join(SCOPES) + " https://www.googleapis.com/auth/gmail.send"}
        with self.assertRaises(AuthError) as cm:
            self._run(self._echo_state, posted=wide)
        self.assertIn("wider than requested", str(cm.exception))

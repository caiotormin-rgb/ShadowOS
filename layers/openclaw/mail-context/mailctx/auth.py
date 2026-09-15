"""OAuth broker. The agent never sees a token.

Two rules shape this file:

1. The token is a capability. It lives in one file at mode 0600 and is handed
   only to the transport in `mailctx.gmail`. Nothing returns it to a caller,
   prints it, or logs it -- `__repr__` is overridden so an accidental repr in a
   traceback cannot leak it either.
2. The loopback flow uses PKCE. A desktop client secret is not confidential
   (Google says so plainly), so the proof-of-possession has to come from
   somewhere else.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# Only what phase 1 approved. Adding to this list is a deliberate act that the
# no-send test will notice.
SCOPES = ("https://www.googleapis.com/auth/gmail.readonly",)

CONFIG_DIR = Path.home() / ".config" / "mail-context"
CLIENT_PATH = CONFIG_DIR / "client_secret.json"
TOKEN_PATH = CONFIG_DIR / "token.json"

# Sibling layers (calendar-context, drive-context) reuse this broker with their
# own scope tuple and token file, sharing the one Desktop OAuth client so the
# operator consents once rather than three times. Each layer keeps its own
# transport-level ceiling; a shared token does not mean a shared capability.

AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"

REFRESH_SKEW = 120  # refresh this many seconds before nominal expiry


class AuthError(RuntimeError):
    """Raised with an error *class*, never with credential material."""


@dataclass(frozen=True)
class ClientConfig:
    client_id: str
    client_secret: str
    project_id: str

    def __repr__(self) -> str:  # never let a traceback print the secret
        return f"ClientConfig(client_id={self.client_id[:12]}..., secret=<redacted>)"

    @classmethod
    def load(cls, path: Path = CLIENT_PATH) -> "ClientConfig":
        try:
            raw = json.loads(path.read_text())
        except FileNotFoundError:
            raise AuthError(f"no client config at {path}; see the Google Cloud handoff") from None
        except json.JSONDecodeError:
            raise AuthError("client config is not valid JSON") from None
        if "installed" not in raw:
            raise AuthError("client config is not a Desktop-app client (no 'installed' key)")
        d = raw["installed"]
        missing = [k for k in ("client_id", "client_secret") if not d.get(k)]
        if missing:
            raise AuthError(f"client config missing: {', '.join(missing)}")
        return cls(d["client_id"], d["client_secret"], d.get("project_id", ""))


class Token:
    """An access/refresh token pair. Deliberately not a dataclass: no free repr."""

    __slots__ = ("_access", "_refresh", "expires_at", "scopes")

    def __init__(self, access: str, refresh: str, expires_at: int, scopes: tuple[str, ...]):
        self._access = access
        self._refresh = refresh
        self.expires_at = expires_at
        self.scopes = scopes

    def __repr__(self) -> str:
        return f"<Token expires_at={self.expires_at} scopes={len(self.scopes)} value=<redacted>>"

    __str__ = __repr__

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at - REFRESH_SKEW

    def to_json(self) -> str:
        return json.dumps({
            "access_token": self._access,
            "refresh_token": self._refresh,
            "expires_at": self.expires_at,
            "scopes": list(self.scopes),
        }, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "Token":
        d = json.loads(text)
        return cls(d["access_token"], d["refresh_token"], int(d["expires_at"]),
                   tuple(d.get("scopes", ())))


class TokenStore:
    def __init__(self, path: Path = TOKEN_PATH):
        self.path = path

    def load(self) -> Token | None:
        try:
            return Token.from_json(self.path.read_text())
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, KeyError):
            raise AuthError("token file is corrupt; re-run authorization") from None

    def save(self, token: Token) -> None:
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        # Write private-then-rename so the token is never briefly world-readable.
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, token.to_json().encode())
        finally:
            os.close(fd)
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)

    def clear(self) -> bool:
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return False


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _post_form(url: str, fields: dict[str, str]) -> dict:
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # Google puts the reason in the body; surface the class, never the body,
        # because the body can echo request parameters.
        try:
            cls = json.loads(exc.read()).get("error", "http_error")
        except Exception:
            cls = "http_error"
        raise AuthError(f"token endpoint rejected the request: {cls}") from None
    except urllib.error.URLError:
        raise AuthError("token endpoint unreachable") from None


class _CallbackHandler(BaseHTTPRequestHandler):
    result: dict = {}

    def do_GET(self) -> None:  # noqa: N802
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _CallbackHandler.result = {k: v[0] for k, v in params.items()}
        ok = "code" in _CallbackHandler.result
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = ("Authorized. You can close this tab and return to the terminal."
               if ok else "Authorization failed. Return to the terminal.")
        self.wfile.write(f"<!doctype html><meta charset=utf-8><title>mail-context</title>"
                         f"<body style='font:16px system-ui;padding:3rem'>{msg}</body>".encode())

    def log_message(self, *args: object) -> None:
        pass  # the default handler logs the query string, which carries the code


def authorize_interactive(client: ClientConfig | None = None, *,
                          store: TokenStore | None = None,
                          open_browser: bool = True,
                          port: int = 0,
                          scopes: tuple[str, ...] = SCOPES,
                          manual: bool = False) -> Token:
    """Run the loopback consent flow.

    Three ways to complete the redirect, in order of preference:

    * `manual=True` -- no listener at all. The operator completes consent in any
      browser anywhere, the redirect fails to load, and they paste the failed
      URL back. The code is in its query string, so nothing needs to connect.
      This is the only mode that works when the browser and this process cannot
      reach each other, and it has no ports to collide.
    * `port=0` -- bind a free port and open a browser here. Needs a desktop.
    * `port=N` with an SSH forward from the operator's laptop. Note the forward
      must originate on the laptop: run from this host it binds N *here* and
      steals the port from the listener below.
    """
    client = client or ClientConfig.load()
    store = store or TokenStore()

    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = _b64url(secrets.token_bytes(16))

    server = None
    if not manual:
        server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
        port = server.server_address[1]
    else:
        port = port or 8766  # nothing listens; the value only has to match Google
    redirect_uri = f"http://localhost:{port}"

    params = {
        "client_id": client.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "access_type": "offline",
        "prompt": "consent",  # force a refresh_token even on re-authorization
    }
    url = AUTH_URI + "?" + urllib.parse.urlencode(params)

    print("Open this URL and grant access:\n\n" + url + "\n")
    if open_browser and not manual:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    if manual:
        print("Your browser will then fail to load a http://localhost address.")
        print("That is expected -- nothing is listening. Copy that failed URL")
        print("from the address bar and paste it here.\n")
        try:
            pasted = input("redirect URL: ").strip()
        except EOFError:
            raise AuthError("no redirect URL provided") from None
        if not pasted:
            raise AuthError("no redirect URL provided")
        parsed = urllib.parse.urlparse(pasted)
        if not parsed.query:
            raise AuthError("that URL has no query string; paste the full failed URL")
        result = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
    else:
        _CallbackHandler.result = {}
        server.timeout = 300
        server.handle_request()
        result = _CallbackHandler.result
        server.server_close()

    if not result:
        raise AuthError("timed out waiting for the consent redirect")
    if result.get("state") != state:
        raise AuthError("state mismatch on the consent redirect")
    if "code" not in result:
        raise AuthError(f"consent denied: {result.get('error', 'unknown')}")

    payload = _post_form(TOKEN_URI, {
        "client_id": client.client_id,
        "client_secret": client.client_secret,
        "code": result["code"],
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    })
    if "refresh_token" not in payload:
        raise AuthError("Google returned no refresh_token; re-consent with prompt=consent")

    granted = tuple(payload.get("scope", "").split()) or scopes
    unexpected = set(granted) - set(scopes)
    if unexpected:
        raise AuthError(f"refusing a grant wider than requested: {sorted(unexpected)}")

    token = Token(payload["access_token"], payload["refresh_token"],
                  int(time.time()) + int(payload.get("expires_in", 3600)), granted)
    store.save(token)
    return token


class AccessTokenProvider:
    """Hands a *live* access token to the transport and to nothing else."""

    def __init__(self, client: ClientConfig | None = None, store: TokenStore | None = None,
                 *, post_form=_post_form):
        self._client = client or ClientConfig.load()
        self._store = store or TokenStore()
        self._post_form = post_form
        self._token: Token | None = None
        # bearer() is called from concurrent fetch workers; without this two of
        # them can refresh at once and the loser's refresh overwrites the winner.
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return "<AccessTokenProvider token=<redacted>>"

    def _current(self) -> Token:
        if self._token is None:
            self._token = self._store.load()
        if self._token is None:
            raise AuthError("not authorized yet; run the consent flow")
        return self._token

    def refresh(self) -> Token:
        token = self._current()
        payload = self._post_form(TOKEN_URI, {
            "client_id": self._client.client_id,
            "client_secret": self._client.client_secret,
            "refresh_token": token._refresh,
            "grant_type": "refresh_token",
        })
        new = Token(payload["access_token"], token._refresh,
                    int(time.time()) + int(payload.get("expires_in", 3600)), token.scopes)
        self._store.save(new)
        self._token = new
        return new

    def bearer(self) -> str:
        """The only method that yields the raw value, and only to a caller that
        already holds this object -- which is the transport, never the agent."""
        with self._lock:
            token = self._current()
            if token.expired:
                token = self.refresh()
            return token._access

    def status(self) -> dict:
        """Safe to log and to surface to the agent."""
        try:
            token = self._current()
        except AuthError as exc:
            return {"authorized": False, "error_class": str(exc).split(";")[0]}
        return {
            "authorized": True,
            "expires_in": max(0, int(token.expires_at - time.time())),
            "expired": token.expired,
            "scopes": list(token.scopes),
        }

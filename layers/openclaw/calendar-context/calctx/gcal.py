"""Google Calendar transport. Read-only by construction, not by discipline.

The plan's hardest requirement is that V1 cannot write -- not an event, not an
RSVP, not a reminder. That is enforced in four independent places here, so a
bug in any one of them is not sufficient to create a write path:

1. `METHOD` is the only HTTP verb this module knows, and `_request` raises
   `ForbiddenMethod` on anything else before a socket is opened.
2. `ALLOWED` is a tuple of anchored regexes. A path that does not fully match
   one of them raises `ForbiddenEndpoint`.
3. `FORBIDDEN` re-checks for mutating segments (`/import`, `/move`,
   `/quickAdd`, `/acl`, `/clear`, `/watch`, ...), so a future careless addition
   to `ALLOWED` still trips.
4. Calendar ids are percent-encoded with `safe=""` and an encoded slash is
   rejected outright, so a hostile calendar id cannot smuggle in a second path
   segment.

The OAuth grant is two Calendar *readonly* scopes, which makes a write
impossible server-side too. That is the real boundary; the above is what keeps
us honest before we get there, and what a test can assert offline.

The one incompatibility that quietly corrupts an index
-----------------------------------------------------
`events.list` forbids `timeMin`/`timeMax` together with `syncToken`. Google does
not error loudly on every client library, and a token silently carries the
bounds of the request that minted it. `list_events` refuses the combination
outright so the mistake cannot be made in the first place; the drift that
results from a rolling window over a fixed anchor is handled in `calctx.sync`.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterator

from .auth import AccessTokenProvider, AuthError

BASE = "https://www.googleapis.com/calendar/v3"

# The only verb in this module. There is no second value anywhere in the file.
METHOD = "GET"

_CAL = r"[A-Za-z0-9%._~@-]+"      # percent-encoded calendar id
_EVENT = r"[A-Za-z0-9%_-]+"       # Google event ids are base32hex-ish

ALLOWED = (
    re.compile(r"^/users/me/calendarList$"),
    re.compile(rf"^/calendars/{_CAL}/events$"),
    re.compile(rf"^/calendars/{_CAL}/events/{_EVENT}/instances$"),
)

# Mutating Calendar segments, plus %2f which would let an id become a path.
FORBIDDEN = re.compile(
    r"(/import|/move|/quickAdd|/acl|/clear|/watch|/stop|/freeBusy|/settings|sendUpdates|%2f)",
    re.IGNORECASE,
)


class TransportError(RuntimeError):
    """Carries an error class and status, never a response body."""

    def __init__(self, error_class: str, status: int | None = None):
        self.error_class = error_class
        self.status = status
        super().__init__(f"{error_class}" + (f" (HTTP {status})" if status else ""))


class ForbiddenEndpoint(RuntimeError):
    """The ceiling refused to build a request. This is never recoverable."""


class ForbiddenMethod(RuntimeError):
    """Something asked for a verb other than GET. Also never recoverable."""


class IncompatibleParameters(ValueError):
    """A parameter combination Google forbids, refused before the call."""


def _default_opener(method: str, url: str, headers: dict[str, str],
                    timeout: int) -> tuple[int, bytes]:
    """The only function in the package that opens a socket to Google.

    `method` is threaded through so a synthetic transport in tests observes the
    verb behaviourally, rather than the test having to trust this docstring.
    The Request below is still a literal constant, which is what the AST check
    in tests/test_gcal_ceiling.py inspects.
    """
    if method != METHOD:
        raise ForbiddenMethod(f"calendar-context issues {METHOD} only, refused: {method}")
    req = urllib.request.Request(url, method="GET", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


class CalendarReadOnly:
    """Every Google Calendar call the system is allowed to make.

    Three endpoints. There is no fourth, and nothing here returns a handle a
    caller could use to reach a fifth.
    """

    def __init__(self, tokens: AccessTokenProvider, *, opener: Callable = _default_opener,
                 timeout: int = 30):
        self._tokens = tokens
        self._opener = opener
        self._timeout = timeout
        self.calls = 0

    # -- the ceiling -----------------------------------------------------
    @staticmethod
    def _check(path: str) -> None:
        if FORBIDDEN.search(path):
            raise ForbiddenEndpoint(f"mutating segment in path: {path}")
        if not any(rx.fullmatch(path) for rx in ALLOWED):
            raise ForbiddenEndpoint(f"path not on the read-only allowlist: {path}")

    @staticmethod
    def _cal(calendar_id: str) -> str:
        return urllib.parse.quote(calendar_id, safe="")

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None) -> dict:
        if method != METHOD:
            raise ForbiddenMethod(f"calendar-context issues {METHOD} only, refused: {method}")
        self._check(path)
        url = BASE + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean, doseq=True)
        headers = {"Authorization": f"Bearer {self._tokens.bearer()}",
                   "Accept": "application/json"}
        self.calls += 1
        try:
            status, body = self._opener(method, url, headers, self._timeout)
        except urllib.error.HTTPError as exc:
            status = exc.code
            if status == 401:
                raise TransportError("auth", status) from None
            if status == 403:
                raise TransportError("forbidden_or_quota", status) from None
            if status == 404:
                raise TransportError("not_found", status) from None
            if status == 410:
                # An expired syncToken. Documented, expected, recoverable --
                # calctx.sync discards the token and re-anchors.
                raise TransportError("gone", status) from None
            if status == 429:
                raise TransportError("rate_limited", status) from None
            raise TransportError("http_error", status) from None
        except urllib.error.URLError:
            raise TransportError("network") from None
        except AuthError:
            raise TransportError("auth") from None
        return json.loads(body)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        return self._request(METHOD, path, params)

    # -- the entire read surface ----------------------------------------
    def calendar_list(self, *, page_token: str | None = None, max_results: int = 250) -> dict:
        """calendarList carries `selected` and `timeZone`, which is why the
        broader `calendars.get` scope is neither needed nor requested."""
        return self._get("/users/me/calendarList", {
            "pageToken": page_token,
            "maxResults": max_results,
            "showDeleted": "true",
            "showHidden": "true",
        })

    def list_events(self, calendar_id: str, *, time_min: str | None = None,
                    time_max: str | None = None, sync_token: str | None = None,
                    page_token: str | None = None, max_results: int = 250) -> dict:
        """The series layer. `singleEvents=false` is not optional here.

        With `singleEvents=true` Google expands recurrences and never sends the
        RRULE, which would make the series layer unauditable and the override
        bookkeeping impossible. Expansion happens in `instances()` instead,
        where it is bounded and attributable to a master.
        """
        if sync_token and (time_min or time_max):
            raise IncompatibleParameters(
                "events.list forbids timeMin/timeMax with syncToken; the token already "
                "carries the bounds of the request that minted it"
            )
        return self._get(f"/calendars/{self._cal(calendar_id)}/events", {
            "timeMin": time_min,
            "timeMax": time_max,
            "syncToken": sync_token,
            "pageToken": page_token,
            "maxResults": max_results,
            "singleEvents": "false",
            "showDeleted": "true",
        })

    def instances(self, calendar_id: str, event_id: str, *, time_min: str,
                  time_max: str, page_token: str | None = None,
                  max_results: int = 250) -> dict:
        """Materialize one series inside the window. Google does the expansion.

        V1 deliberately ships no RRULE engine: a hand-rolled expander over
        BYSETPOS, BYDAY, UNTIL semantics, DST transitions, and leap-day
        anchoring is the likeliest single source of a silently wrong agenda,
        and it would be wrong in a way no synthetic fixture discovers.

        `showDeleted=true` is what makes "this occurrence was cancelled"
        distinguishable from "this occurrence never existed" -- two states that
        give different answers to "am I free Thursday?".
        """
        return self._get(
            f"/calendars/{self._cal(calendar_id)}/events/{urllib.parse.quote(event_id, safe='')}/instances",
            {
                "timeMin": time_min,
                "timeMax": time_max,
                "pageToken": page_token,
                "maxResults": max_results,
                "showDeleted": "true",
            },
        )

    # -- pagination helper ----------------------------------------------
    def paginate(self, fn: Callable[..., dict], key: str, **kwargs) -> Iterator[dict]:
        """Walk pages, yielding items. The caller keeps the final page's
        `nextSyncToken`; this helper deliberately does not, because storing a
        token is a transaction-ordering decision that belongs in calctx.sync."""
        token = None
        while True:
            page = fn(page_token=token, **kwargs)
            for item in page.get(key, []):
                yield item
            token = page.get("nextPageToken")
            if not token:
                return

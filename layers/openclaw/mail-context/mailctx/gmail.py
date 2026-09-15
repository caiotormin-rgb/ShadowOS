"""Gmail transport. Read-only by construction, not by discipline.

The plan's hard requirement is that V1 cannot send. That is enforced in three
independent places here, so a bug in any one of them is not sufficient to
create a send path:

1. `ALLOWED` is a tuple of anchored regexes. A request whose path does not
   fully match one of them raises before any socket is opened.
2. `_get` is the only method that performs I/O and it hardcodes ``method="GET"``.
   There is no code path in this module that issues POST, PUT, PATCH or DELETE.
3. `FORBIDDEN` re-checks for mutating segments, so a future careless addition to
   `ALLOWED` still trips.

The OAuth grant is `gmail.readonly`, which makes a send impossible server-side
too. That is the real boundary; the above is what keeps us honest before we get
there.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterator

from .auth import AccessTokenProvider, AuthError

BASE = "https://gmail.googleapis.com/gmail/v1"

ALLOWED = (
    re.compile(r"^/users/me/profile$"),
    re.compile(r"^/users/me/labels$"),
    re.compile(r"^/users/me/messages$"),
    re.compile(r"^/users/me/messages/[A-Za-z0-9_-]+$"),
    # Read-only attachment bytes for the artifact pilot; GET only, no state change.
    re.compile(r"^/users/me/messages/[A-Za-z0-9_-]+/attachments/[A-Za-z0-9_-]+$"),
    re.compile(r"^/users/me/threads/[A-Za-z0-9_-]+$"),
    re.compile(r"^/users/me/history$"),
)

FORBIDDEN = re.compile(r"(send|trash|untrash|modify|batchModify|batchDelete|drafts|settings|watch|stop)",
                       re.IGNORECASE)


class TransportError(RuntimeError):
    """Carries an error class and status, never a response body."""

    def __init__(self, error_class: str, status: int | None = None):
        self.error_class = error_class
        self.status = status
        super().__init__(f"{error_class}" + (f" (HTTP {status})" if status else ""))


class ForbiddenEndpoint(RuntimeError):
    """The ceiling refused to build a request. This is never recoverable."""


def _default_opener(url: str, headers: dict[str, str], timeout: int) -> tuple[int, bytes]:
    req = urllib.request.Request(url, method="GET", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


RATE_LIMIT_REASONS = {"rateLimitExceeded", "userRateLimitExceeded",
                      "quotaExceeded", "backendError"}


class RateLimiter:
    """Token bucket. Gmail allows 250 quota units/s per user and a metadata GET
    costs 5, so the ceiling is 50/s. A measured 51 msg/s tripped it, so the
    default target sits deliberately below."""

    def __init__(self, per_second: float = 35.0):
        self.min_interval = 1.0 / per_second if per_second > 0 else 0.0
        # The interval a caller asked for. back_off() only ever widens, so
        # without a remembered floor a single early burst of 403s would slow a
        # multi-hour bulk run for its whole duration.
        self.floor_interval = self.min_interval
        self._lock = threading.Lock()
        self._next = 0.0

    def acquire(self, sleep=time.sleep) -> None:
        if not self.min_interval:
            return
        with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.min_interval
        if wait:
            sleep(wait)

    def back_off(self, factor: float = 1.5) -> None:
        """Widen the interval after a rate-limit response. The load is long
        enough that recovering slowly beats failing fast."""
        with self._lock:
            self.min_interval = min(self.min_interval * factor, 1.0)


class GmailReadOnly:
    """Every Gmail call the system is allowed to make."""

    def __init__(self, tokens: AccessTokenProvider, *, opener: Callable = _default_opener,
                 timeout: int = 30, rate_limiter: "RateLimiter | None" = None):
        self._tokens = tokens
        self._opener = opener
        self._timeout = timeout
        self.limiter = rate_limiter if rate_limiter is not None else RateLimiter()
        self.calls = 0
        self.rate_limit_hits = 0

    @staticmethod
    def _classify_403(exc) -> str:
        """Read only the `reason`/`status` field from a 403 body. The body can
        echo request parameters, so nothing else is touched or logged."""
        try:
            body = json.loads(exc.read())
        except Exception:
            return "forbidden"
        err = body.get("error", {})
        reasons = {e.get("reason", "") for e in err.get("errors", [])}
        reasons.add(err.get("status", ""))
        return "rate_limited" if reasons & RATE_LIMIT_REASONS else "forbidden"

    # -- the ceiling -----------------------------------------------------
    @staticmethod
    def _check(path: str) -> None:
        if FORBIDDEN.search(path):
            raise ForbiddenEndpoint(f"mutating segment in path: {path}")
        if not any(rx.fullmatch(path) for rx in ALLOWED):
            raise ForbiddenEndpoint(f"path not on the read-only allowlist: {path}")

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        self._check(path)
        url = BASE + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean, doseq=True)
        headers = {"Authorization": f"Bearer {self._tokens.bearer()}",
                   "Accept": "application/json"}
        self.limiter.acquire()
        self.calls += 1
        try:
            status, body = self._opener(url, headers, self._timeout)
        except urllib.error.HTTPError as exc:
            status = exc.code
            if status == 401:
                raise TransportError("auth", status) from None
            if status == 403:
                # Gmail reports per-user rate limiting as 403, NOT 429. Only the
                # machine-readable `reason` distinguishes it from a genuine
                # permission denial, so we read that one field -- and nothing
                # else -- out of the error body.
                cls = self._classify_403(exc)
                if cls == "rate_limited":
                    self.rate_limit_hits += 1
                    self.limiter.back_off()
                raise TransportError(cls, status) from None
            if status == 404:
                raise TransportError("not_found", status) from None
            if status == 429:
                self.rate_limit_hits += 1
                self.limiter.back_off()
                raise TransportError("rate_limited", status) from None
            raise TransportError("http_error", status) from None
        except urllib.error.URLError:
            raise TransportError("network") from None
        except AuthError:
            raise TransportError("auth") from None
        return json.loads(body)

    # -- the entire read surface ----------------------------------------
    def profile(self) -> dict:
        return self._get("/users/me/profile")

    def labels(self) -> list[dict]:
        return self._get("/users/me/labels").get("labels", [])

    def list_message_ids(self, *, query: str | None = None, page_token: str | None = None,
                         max_results: int = 500, include_spam_trash: bool = False) -> dict:
        return self._get("/users/me/messages", {
            "q": query,
            "pageToken": page_token,
            "maxResults": max_results,
            "includeSpamTrash": "true" if include_spam_trash else "false",
        })

    def message_metadata(self, message_id: str) -> dict:
        """format=metadata is what keeps bodies out of the process entirely --
        Google does not send them, so they cannot be logged or cached by mistake."""
        return self._get(f"/users/me/messages/{message_id}", {
            "format": "metadata",
            "metadataHeaders": ["From", "To", "Cc", "Reply-To", "Subject", "Date"],
        })

    def message_full(self, message_id: str) -> dict:
        """format=full: bodies and inline attachment data. Used only by the
        artifact fetch path, which stores into the encrypted store (or a
        shredded pilot dir) -- never into the metadata index."""
        return self._get(f"/users/me/messages/{message_id}", {"format": "full"})

    def attachment(self, message_id: str, attachment_id: str) -> dict:
        return self._get(f"/users/me/messages/{message_id}/attachments/{attachment_id}")

    def history(self, start_history_id: int, *, page_token: str | None = None,
                max_results: int = 500) -> dict:
        return self._get("/users/me/history", {
            "startHistoryId": start_history_id,
            "pageToken": page_token,
            "maxResults": max_results,
            "historyTypes": ["messageAdded", "messageDeleted", "labelAdded", "labelRemoved"],
        })

    # -- pagination helper ----------------------------------------------
    def paginate(self, fn: Callable[..., dict], key: str, **kwargs) -> Iterator[dict]:
        token = None
        while True:
            page = fn(page_token=token, **kwargs)
            for item in page.get(key, []):
                yield item
            token = page.get("nextPageToken")
            if not token:
                return

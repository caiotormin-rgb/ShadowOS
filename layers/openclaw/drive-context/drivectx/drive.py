"""Drive transport. Metadata-only by construction, not by discipline.

The plan's hard requirement is that V1 cannot read file content. The OAuth
grant is `drive.metadata.readonly`, under which Google itself rejects a content
read -- that is the real boundary, and it is a boundary that survives every
future careless edit to this package. Everything below is the tripwire that
tells us when someone tried anyway:

1. `ALLOWED` is a tuple of anchored regexes over the path. A request whose path
   does not fully match one of them raises before any socket is opened.
2. `PARAMS` is an allowlist of query-parameter *names*. Drive's content door is
   a parameter, not a path -- `?alt=media` on the ordinary files.get endpoint --
   so a path-only ceiling of the kind mail-context needs would be worthless
   here. `alt`, `acknowledgeAbuse` and `uploadType` are absent from the list, so
   they cannot be sent even by accident.
3. `FORBIDDEN` re-checks the fully assembled request line, percent-decoded, for
   content-bearing segments and parameters, so a careless addition to either
   allowlist still trips.
4. `FORBIDDEN_FIELDS` checks the field mask itself, because a field mask is how
   you ask Drive for a thumbnail, a checksum, a capability-bearing link, or a
   grantee's email address.
5. `_get` is the only method that performs I/O and it hardcodes ``method="GET"``.
   There is no code path in this module that issues POST, PUT, PATCH or DELETE.

tests/test_drive_ceiling.py asserts 1-5 structurally, including an AST walk over
every `urllib.request.Request` call in this module.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterator

from .auth import AuthError

BASE = "https://www.googleapis.com/drive/v3"

ALLOWED = (
    re.compile(r"^/about$"),
    re.compile(r"^/files$"),
    re.compile(r"^/files/[A-Za-z0-9_-]+$"),
    re.compile(r"^/changes$"),
    re.compile(r"^/changes/startPageToken$"),
)

# Every query parameter this layer is permitted to send. Note what is missing:
# `alt` (the media-download switch), `acknowledgeAbuse` (only meaningful for a
# download), and `uploadType` (only meaningful for a write).
PARAMS = frozenset({
    "corpora", "fields", "includeItemsFromAllDrives", "includeRemoved",
    "orderBy", "pageSize", "pageToken", "q", "restrictToMyDrive", "spaces",
    "supportsAllDrives",
})

# The sub-resource alternatives are followed by a delimiter or end-of-string so
# that a Drive file id which merely begins with "copy" is not mistaken for the
# /copy verb. A false refusal here would be a silent gap in the index.
FORBIDDEN = re.compile(
    r"(alt\s*=\s*media|uploadType|acknowledgeAbuse|resourceKey|"
    r"/(?:export|revisions|comments|replies|permissions|watch|copy|trash|"
    r"untrash|generateIds|emptyTrash|upload)(?:[/?#]|$))",
    re.IGNORECASE,
)

# A field mask is a request for data. These are the fields whose presence would
# mean this layer had started holding content, a content identifier, a
# capability-bearing URL, or a third party's address.
FORBIDDEN_FIELDS = re.compile(
    r"(thumbnail|iconLink|md5|sha1|sha256|checksum|webContentLink|exportLinks|"
    r"contentHints|resourceKey|permissions?\([^)]*emailAddress)",
    re.IGNORECASE,
)

# The complete set of file fields this layer ever asks Drive for. Nothing here
# is content, a content hash, or a grantee address: the permissions sub-mask
# asks for type, role and discoverability only, which is what makes the sharing
# summary derivable *without* ever receiving the social graph in the first
# place. Minimizing at the request is stronger than minimizing at the store.
FILE_FIELDS = (
    "id,name,mimeType,parents,owners(displayName,emailAddress),"
    "createdTime,modifiedTime,size,trashed,explicitlyTrashed,starred,"
    "webViewLink,shortcutDetails(targetId,targetMimeType),ownedByMe,"
    "driveId,shared,permissions(type,role,allowFileDiscovery,deleted)"
)

FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"

# Reasons Google returns are short enum-ish tokens, but they arrive in a
# response body that can echo request parameters, so only the sanitized token is
# ever kept and the body itself is discarded unread.
_REASON = re.compile(r"^[A-Za-z]{1,40}$")


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


def _reason_of(exc: urllib.error.HTTPError) -> str:
    """Pull the machine-readable reason out of a Google error, and nothing else.

    Distinguishing an expired page token from an ordinary bad request needs the
    reason, and the reason is only in the body. So the body is parsed, one short
    token is extracted, that token is validated against a strict pattern, and
    everything else is dropped on the floor.
    """
    try:
        payload = json.loads(exc.read() or b"{}")
    except Exception:
        return ""
    err = payload.get("error")
    if not isinstance(err, dict):
        return ""
    candidates = [e.get("reason", "") for e in err.get("errors", [])
                  if isinstance(e, dict)]
    candidates.append(err.get("status", ""))
    for c in candidates:
        if isinstance(c, str) and _REASON.match(c):
            return c
    return ""


class DriveReadOnly:
    """Every Drive call the system is allowed to make.

    include_shared_drives is a named setting with a default of false, not an
    accident of which API flags were passed. It is threaded through the request
    parameters here and re-checked per row in drivectx.sync, because a flag that
    only lives in one of those two places is a flag that will eventually be
    wrong in the other.
    """

    def __init__(self, tokens, *, opener: Callable = _default_opener, timeout: int = 30,
                 include_shared_drives: bool = False):
        self._tokens = tokens
        self._opener = opener
        self._timeout = timeout
        self.include_shared_drives = bool(include_shared_drives)
        self.calls = 0

    # -- the ceiling -----------------------------------------------------
    @staticmethod
    def _check(path: str, params: dict[str, Any] | None = None) -> str:
        """Validate and return the query string. Raises before any I/O."""
        if not any(rx.fullmatch(path) for rx in ALLOWED):
            raise ForbiddenEndpoint(f"path not on the metadata-only allowlist: {path}")

        clean = {k: v for k, v in (params or {}).items() if v is not None}
        unknown = sorted(set(clean) - PARAMS)
        if unknown:
            raise ForbiddenEndpoint(f"query parameter not on the allowlist: {unknown}")

        mask = str(clean.get("fields", ""))
        if FORBIDDEN_FIELDS.search(mask):
            raise ForbiddenEndpoint("field mask asks for content, a checksum, a "
                                    "capability URL, or a grantee address")

        query = urllib.parse.urlencode(clean, doseq=True) if clean else ""
        line = path + ("?" + query if query else "")
        # Percent-decode to a fixed point before the final check, so `alt=media`
        # smuggled into a parameter value cannot slip past by being encoded once
        # by the caller and once again by urlencode.
        for _ in range(4):
            if FORBIDDEN.search(line):
                raise ForbiddenEndpoint(f"content or mutating element in request: {path}")
            decoded = urllib.parse.unquote(line)
            if decoded == line:
                break
            line = decoded
        return query

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        query = self._check(path, params)
        url = BASE + path + ("?" + query if query else "")
        headers = {"Authorization": f"Bearer {self._tokens.bearer()}",
                   "Accept": "application/json"}
        self.calls += 1
        try:
            status, body = self._opener(url, headers, self._timeout)
        except urllib.error.HTTPError as exc:
            raise self._classify(exc) from None
        except urllib.error.URLError:
            raise TransportError("network") from None
        except AuthError:
            raise TransportError("auth") from None
        return json.loads(body)

    @staticmethod
    def _classify(exc: urllib.error.HTTPError) -> TransportError:
        """Map an HTTP failure to an error class.

        `stale_page_token` is the one that matters operationally: Drive rejects
        an expired Changes cursor rather than serving it, as 410, or as a 400
        whose reason names the page token. Both are an expected condition with a
        documented recovery, not a failure.
        """
        status = exc.code
        reason = _reason_of(exc)
        low = reason.lower()
        if status == 410:
            return TransportError("stale_page_token", status)
        if status == 400:
            if "pagetoken" in low or low == "invalid":
                return TransportError("stale_page_token", status)
            return TransportError("bad_request", status)
        if status == 401:
            return TransportError("auth", status)
        if status == 403:
            # Drive reports per-user rate limiting as 403, not only as 429. Only
            # the machine-readable reason separates it from a genuine permission
            # denial, and treating one as the other either fails a healthy run or
            # retries a hopeless one.
            if "rate" in low or "quota" in low or "limit" in low:
                return TransportError("rate_limited", status)
            return TransportError("forbidden", status)
        if status == 404:
            return TransportError("not_found", status)
        if status == 429:
            return TransportError("rate_limited", status)
        return TransportError("http_error", status)

    # -- the entire read surface ----------------------------------------
    def about(self) -> dict:
        """Account-level facts. Used for a storage-quota sanity check and to
        confirm which account the grant belongs to."""
        return self._get("/about", {"fields": "user(displayName,emailAddress),storageQuota"})

    def root_folder_id(self) -> str:
        """My Drive's root id. Path resolution needs it to tell 'reached the
        top' apart from 'this parent is not in the index'."""
        return self._get("/files/root", {"fields": "id"})["id"]

    def start_page_token(self) -> str:
        params = {"fields": "startPageToken"}
        params.update(self._drive_flags())
        return self._get("/changes/startPageToken", params)["startPageToken"]

    def list_files(self, *, page_token: str | None = None, page_size: int = 1000,
                   query: str | None = None) -> dict:
        """One page of the bounded initial listing.

        Drive returns full metadata inline, so unlike Gmail there is no
        per-item second request and no N+1 to parallelise away.
        """
        params = {
            "fields": f"nextPageToken,files({FILE_FIELDS})",
            "pageSize": page_size,
            "pageToken": page_token,
            "q": query,
            "spaces": "drive",
            "orderBy": "modifiedTime desc",
        }
        params.update(self._drive_flags())
        return self._get("/files", params)

    def get_file(self, file_id: str) -> dict:
        """One file's current metadata. Used for the operator's explicit
        re-check path and for ids absent from the index."""
        params = {"fields": FILE_FIELDS}
        params.update(self._drive_flags(listing=False))
        return self._get(f"/files/{file_id}", params)

    def list_changes(self, page_token: str, *, page_size: int = 1000) -> dict:
        params = {
            "pageToken": page_token,
            "pageSize": page_size,
            "fields": ("nextPageToken,newStartPageToken,"
                       f"changes(fileId,removed,time,changeType,driveId,file({FILE_FIELDS}))"),
            "spaces": "drive",
            "includeRemoved": "true",
        }
        params.update(self._drive_flags())
        return self._get("/changes", params)

    # -- helpers ---------------------------------------------------------
    def _drive_flags(self, *, listing: bool = True) -> dict[str, str]:
        """The shared-drive exclusion, expressed as request parameters.

        `restrictToMyDrive` is only meaningful on listing endpoints; sending it
        elsewhere would be an unknown parameter to Drive.
        """
        on = self.include_shared_drives
        flags = {
            "includeItemsFromAllDrives": "true" if on else "false",
            "supportsAllDrives": "true" if on else "false",
        }
        if listing:
            flags["restrictToMyDrive"] = "false" if on else "true"
        return flags

    def paginate(self, fn: Callable[..., dict], key: str, **kwargs) -> Iterator[dict]:
        token = None
        while True:
            page = fn(page_token=token, **kwargs)
            for item in page.get(key, []):
                yield item
            token = page.get("nextPageToken")
            if not token:
                return

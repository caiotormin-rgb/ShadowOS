"""Scope and token-file policy for calendar-context.

The OAuth machinery itself is not reimplemented here. `mailctx.auth` already
holds a PKCE loopback broker that never returns a token to a caller, refreshes
under a lock, and writes 0600 files; it is scope-parameterized precisely so
sibling layers can reuse it. This module supplies the two things that must NOT
be shared -- the scope tuple and the token file -- and nothing else.

One Desktop OAuth client is shared across layers so the operator consents once.
A shared client is not a shared capability: the grant recorded in *this* token
file authorizes reads of calendars and events and nothing more, and the ceiling
in `calctx.gcal` is enforced independently of it.

Why these two scopes and not the obvious ones
---------------------------------------------
* `calendar`, `calendar.events`, `calendar.events.owned` all authorize writes.
  Requesting any of them would make "no write path" a property of our code
  rather than of the grant. mail-context already learned that consent is not a
  boundary -- Gmail's compose permission also authorizes sending -- so here we
  pick a grant under which a write is simply not authorized.
* `calendar.readonly` is write-free and would work, but also carries read
  access to Calendar settings and ACLs that V1 never touches. The two granular
  scopes below are a strict subset of it.
* `calendar.events.readonly` alone does not authorize `calendarList.list`, so
  without the calendarList scope the indexer could only ever read a hardcoded
  `primary` calendar and could not discover a calendar's IANA timezone.

Under this grant a POST/PUT/PATCH/DELETE to any Calendar endpoint fails at
Google with 403, including in the presence of a bug, a prompt injection, or a
future careless edit.
"""
from __future__ import annotations

from pathlib import Path

from mailctx.auth import (  # noqa: F401  -- re-exported as this layer's broker
    AccessTokenProvider,
    AuthError,
    ClientConfig,
    Token,
    TokenStore,
    authorize_interactive,
)

SCOPE_PREFIX = "https://www.googleapis.com/auth/"

SCOPES = (
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/calendar.events.readonly",
)

# The plan's only approved fallback, for the case where the granular
# calendarlist scope is not offered on the consent screen. Still write-free.
# Falling back to any non-readonly scope is not approved and voids the plan.
FALLBACK_SCOPES = ("https://www.googleapis.com/auth/calendar.readonly",)

CONFIG_DIR = Path.home() / ".config" / "calendar-context"

# The Desktop client is shared with mail-context so the operator registers and
# consents once; the *token* is not, so revoking one layer cannot be confused
# with revoking the other.
CLIENT_PATH = Path.home() / ".config" / "mail-context" / "client_secret.json"
TOKEN_PATH = CONFIG_DIR / "calendar-token.json"


def token_store(path: Path | None = None) -> TokenStore:
    return TokenStore(path or TOKEN_PATH)


def provider(*, client_path: Path | None = None, token_path: Path | None = None
             ) -> AccessTokenProvider:
    """The transport's credential source. Nothing else should hold one."""
    return AccessTokenProvider(
        ClientConfig.load(client_path or CLIENT_PATH),
        token_store(token_path),
    )


def scopes_are_readonly(scopes: tuple[str, ...]) -> bool:
    """True only if every scope in a grant is a Calendar *readonly* scope.

    Used by the CLI to refuse to run against a token that was minted with a
    wider grant than this layer asked for -- a stored token outlives the code
    that requested it, so re-checking at use time is not redundant.

    The prefix is assembled from `SCOPE_PREFIX` rather than written out whole:
    tests/test_no_write_path.py collects every scope-shaped literal in the
    package and requires each one to end in `.readonly`, and a bare prefix
    written here would read to that scan as a full-access Calendar scope.
    """
    return bool(scopes) and all(
        s.startswith(SCOPE_PREFIX + "calendar") and s.endswith(".readonly")
        for s in scopes
    )

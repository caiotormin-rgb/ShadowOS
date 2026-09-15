"""Operator entry point for the one-time consent flow.

    python3 -m calctx.authorize --port 8765

Run this as the account that will own the token. It writes
~/.config/calendar-context/calendar-token.json at mode 0600 and prints nothing
sensitive.

The consent screen this opens grants **read** on calendars and events. It offers
no write, which is the point: the absence of a write path is a property of the
grant, not only of our code. Capture the rendered permission text as evidence.
"""
from __future__ import annotations

import argparse
import sys

from .auth import (
    FALLBACK_SCOPES,
    SCOPES,
    AccessTokenProvider,
    AuthError,
    ClientConfig,
    authorize_interactive,
    scopes_are_readonly,
    token_store,
)
from .auth import CLIENT_PATH


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Authorize calendar-context against Google Calendar (read-only).")
    ap.add_argument("--port", type=int, default=0,
                    help="pin the loopback callback port (needed when the browser is elsewhere)")
    ap.add_argument("--manual", action="store_true",
                    help="no callback listener: paste the failed redirect URL back "
                         "(use when the browser cannot reach this host)")
    ap.add_argument("--no-browser", action="store_true", help="print the URL instead of opening it")
    ap.add_argument("--status", action="store_true", help="report authorization state and exit")
    ap.add_argument("--fallback-scope", action="store_true",
                    help="use the single calendar.readonly scope, the plan's only approved "
                         "fallback when the granular calendarlist scope is not offered")
    args = ap.parse_args(argv)

    try:
        client = ClientConfig.load(CLIENT_PATH)
    except AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    store = token_store()
    scopes = FALLBACK_SCOPES if args.fallback_scope else SCOPES

    if args.status:
        for k, v in AccessTokenProvider(client, store).status().items():
            print(f"{k:12}: {v}")
        return 0

    # Belt and braces: a non-readonly scope must never reach the consent URL,
    # even if someone edits the tuple above.
    if not scopes_are_readonly(scopes):
        print("error: refusing to request a non-readonly Calendar scope", file=sys.stderr)
        return 2

    print(f"project    : {client.project_id}")
    print(f"client     : {client.client_id[:12]}... (shared with mail-context)")
    print(f"token file : {store.path}")
    print("scopes     : " + ", ".join(s.rsplit('/', 1)[-1] for s in scopes))
    print("grant is read-only; this build has no event, RSVP, or reminder write path.\n")

    try:
        authorize_interactive(client, store=store, open_browser=not args.no_browser,
                              port=args.port, scopes=scopes, manual=args.manual)
    except AuthError as exc:
        print(f"\nauthorization failed: {exc}", file=sys.stderr)
        return 1

    print(f"\nauthorized. token written to {store.path} (0600)")
    for k, v in AccessTokenProvider(client, store).status().items():
        print(f"{k:12}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

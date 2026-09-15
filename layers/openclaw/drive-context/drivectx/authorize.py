"""Operator entry point for the one-time Drive consent flow.

    python3 -m drivectx.authorize --port 8765

Run this as the account that will own the token. It writes
~/.config/mail-context/drive-token.json at mode 0600 -- a different file from
mail-context's own token, sharing only the Desktop OAuth client -- and prints
nothing sensitive.

Two things to check on the consent screen, because they are the whole security
argument of this layer:

* it must name *metadata* only, never "See and download all your Google Drive
  files";
* `drive.metadata.readonly` is a restricted scope, so an OAuth client left in
  Testing publishing status issues refresh tokens that expire after seven days.
  That would silently break a four-times-daily timer within a week. Verify
  refresh-token durability from the noninteractive service account before
  building on it.
"""
from __future__ import annotations

import argparse
import sys

from .auth import (SCOPES, AccessTokenProvider, AuthError, ClientConfig, CLIENT_PATH,
                   authorize_interactive, token_store)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Authorize drive-context against Google Drive (metadata read-only).")
    ap.add_argument("--port", type=int, default=0,
                    help="pin the loopback callback port (needed when the browser is on another machine)")
    ap.add_argument("--manual", action="store_true",
                    help="no callback listener: paste the failed redirect URL back "
                         "(use when the browser cannot reach this host)")
    ap.add_argument("--no-browser", action="store_true", help="print the URL instead of opening it")
    ap.add_argument("--status", action="store_true", help="report authorization state and exit")
    args = ap.parse_args(argv)

    try:
        client = ClientConfig.load(CLIENT_PATH)
    except AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    store = token_store()

    if args.status:
        for k, v in AccessTokenProvider(client, store).status().items():
            print(f"{k:12}: {v}")
        return 0

    print(f"project    : {client.project_id}")
    print(f"client     : {client.client_id[:12]}...")
    print("scopes     : " + ", ".join(s.rsplit('/', 1)[-1] for s in SCOPES))
    print("grant is metadata-only; under this scope Google itself refuses a content read.\n")

    try:
        authorize_interactive(client, store=store, open_browser=not args.no_browser,
                              port=args.port, scopes=SCOPES, manual=args.manual)
    except AuthError as exc:
        print(f"\nauthorization failed: {exc}", file=sys.stderr)
        return 1

    print(f"\nauthorized. token written to {store.path} (0600)")
    for k, v in AccessTokenProvider(client, store).status().items():
        print(f"{k:12}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

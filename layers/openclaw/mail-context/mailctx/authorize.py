"""Operator entry point for the one-time consent flow.

    python3 -m mailctx.authorize --port 8765

Run this as the account that will own the token. It writes
~/.config/mail-context/token.json at mode 0600 and prints nothing sensitive.
"""
from __future__ import annotations

import argparse
import sys

from .auth import SCOPES, AccessTokenProvider, AuthError, ClientConfig, TokenStore, authorize_interactive


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Authorize mail-context against Gmail (read-only).")
    ap.add_argument("--port", type=int, default=0,
                    help="pin the loopback callback port (needed when the browser is on another machine)")
    ap.add_argument("--manual", action="store_true",
                    help="no callback listener: paste the failed redirect URL back "
                         "(use when the browser cannot reach this host)")
    ap.add_argument("--no-browser", action="store_true", help="print the URL instead of opening it")
    ap.add_argument("--status", action="store_true", help="report authorization state and exit")
    args = ap.parse_args(argv)

    try:
        client = ClientConfig.load()
    except AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    store = TokenStore()

    if args.status:
        for k, v in AccessTokenProvider(client, store).status().items():
            print(f"{k:12}: {v}")
        return 0

    print(f"project    : {client.project_id}")
    print(f"client     : {client.client_id[:12]}...")
    print("scopes     : " + ", ".join(s.rsplit('/', 1)[-1] for s in SCOPES))
    print("grant is read-only; this build has no send or modify path.\n")

    try:
        authorize_interactive(client, store=store,
                              open_browser=not args.no_browser, port=args.port, manual=args.manual)
    except AuthError as exc:
        print(f"\nauthorization failed: {exc}", file=sys.stderr)
        return 1

    print(f"\nauthorized. token written to {store.path} (0600)")
    for k, v in AccessTokenProvider(client, store).status().items():
        print(f"{k:12}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

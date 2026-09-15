"""Drive's OAuth identity. The broker itself lives in mail-context.

There is exactly one OAuth broker on this machine (`mailctx.auth`) and one
Desktop OAuth client, so the operator consents once rather than once per layer.
A shared client is not a shared capability: each layer brings its own scope
tuple and its own token file, and the token this module reads cannot be used to
read mail any more than mail-context's token can be used to read Drive.

What this module adds to the broker is the two facts that are specific to Drive:

* `SCOPES` -- metadata only. This is the load-bearing security decision of the
  whole layer. `drive.readonly` would also work and that is precisely the
  problem: it reads content. Under `drive.metadata.readonly` Google itself
  rejects a content read, so the dangerous capability is absent rather than
  merely uncalled.
* `TOKEN_PATH` -- a separate file from mail-context's `token.json`, so revoking
  or losing one grant does not disturb the other.
"""
from __future__ import annotations

from pathlib import Path

# Re-exported so callers import the broker from one place; these are mailctx's
# objects, not copies of them.
from mailctx.auth import (  # noqa: F401
    AccessTokenProvider,
    AuthError,
    ClientConfig,
    Token,
    TokenStore,
    authorize_interactive,
)

# Metadata only, and nothing broader. tests/test_no_content_path.py fails if a
# second googleapis scope ever appears anywhere in this package.
SCOPES = ("https://www.googleapis.com/auth/drive.metadata.readonly",)

# The Desktop client is shared with mail-context; the token is not.
CONFIG_DIR = Path.home() / ".config" / "mail-context"
CLIENT_PATH = CONFIG_DIR / "client_secret.json"
TOKEN_PATH = CONFIG_DIR / "drive-token.json"


def token_store(path: Path = TOKEN_PATH) -> TokenStore:
    """The Drive token store. Separate file, mode 0600, same broker."""
    return TokenStore(path)


def provider(client: ClientConfig | None = None,
             store: TokenStore | None = None) -> AccessTokenProvider:
    """An access-token provider bound to the Drive token file.

    The provider hands the raw value to the transport and to nothing else, and
    its `bearer()` is already lock-guarded for concurrent workers.
    """
    return AccessTokenProvider(client or ClientConfig.load(CLIENT_PATH),
                               store or token_store())

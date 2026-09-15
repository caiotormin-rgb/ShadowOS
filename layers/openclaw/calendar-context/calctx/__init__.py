"""calendar-context: a rebuildable local read model of Google Calendar.

Standard library only, by deliberate choice: torm has no system pip and the
production runtime is an isolated account with no sudo, so a zero-dependency
package can be installed by copying a directory and audited by reading it.

Google Calendar remains authoritative. **Nothing here can write to it.** V1 has
no event creation, no RSVP, no reminder path, and none is deferred to a later
phase -- see calctx.gcal and tests/test_no_write_path.py.

Cross-layer coupling
--------------------
This package imports two modules from the sibling `mail-context` layer:

    mailctx.preflight  -- the encryption-at-rest gate, which is not mail-
                          specific and should not exist twice
    mailctx.auth       -- the OAuth broker, which is scope-parameterized so
                          each layer supplies its own scopes and token file

A shared package extraction is planned but deliberately not attempted here.
Until it happens, `layers/openclaw/mail-context/` must be present next to this
directory. The bridge below makes that a readable one-liner rather than
something a future reader discovers from an ImportError.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCHEMA_VERSION = 1

_SIBLING = Path(__file__).resolve().parent.parent.parent / "mail-context"
if (_SIBLING / "mailctx" / "__init__.py").is_file() and str(_SIBLING) not in sys.path:
    sys.path[:0] = [str(_SIBLING)]

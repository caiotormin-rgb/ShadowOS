"""mail-context: a rebuildable local read model of Gmail metadata.

Standard library only, by deliberate choice: torm has no system pip and the
production runtime is an isolated account with no sudo, so a zero-dependency
package can be installed by copying a directory and audited by reading it.

Gmail remains authoritative. Nothing here may send mail; see mailctx.gmail.
"""

SCHEMA_VERSION = 3

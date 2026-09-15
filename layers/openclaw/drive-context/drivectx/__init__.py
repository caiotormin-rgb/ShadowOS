"""drive-context: a rebuildable local read model of Google Drive metadata.

Standard library only, by deliberate choice: torm has no system pip and the
production runtime is an isolated account with no sudo, so a zero-dependency
package can be installed by copying a directory and audited by reading it.

Drive remains authoritative. Nothing here may read file content; see
drivectx.drive for the transport ceiling and drivectx.auth for the scope that
makes a content read impossible server-side.

This package imports mailctx.preflight and mailctx.auth from the sibling
mail-context layer. That coupling is deliberate and one-way -- mail-context
never imports this package -- and it means drive-context is not installable
without mail-context on PYTHONPATH.
"""

SCHEMA_VERSION = 1

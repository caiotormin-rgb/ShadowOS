"""life-index: an encrypted, local catalog of documents that matter.

Sources are manual drops, a one-off Gmail harvest, and (later) Drive; the
pipeline is identical for all three. Stdlib only in this package -- the one
optional dependency (Docling) is invoked out-of-process, so the catalog
itself stays auditable and installable by copying a directory.
"""
SCHEMA_VERSION = 1

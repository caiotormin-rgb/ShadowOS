"""Import Google Drive documents into the catalog.

Drive files are cataloged **in place**: metadata, extracted text and a link,
but not the bytes. Google already stores the file, already OCRs it, and
already serves search over it — copying it here would mean a second copy of
tier-1 material to protect for no retrieval benefit.

That makes a Drive artifact different from a Gmail one in exactly one way:
`artifact_blobs` has no row, and `source_ref` is a Drive file id. Everything
downstream — search, fields, tiering, the agent tools — is identical.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from . import classify, fields
from .store import Artifact, Catalog

VIEW_URL = "https://drive.google.com/file/d/{}/view"


@dataclass
class DriveDoc:
    file_id: str
    title: str
    mime: str
    created: str | None = None
    doc_type: str | None = None
    tier: int | None = None
    text: str = ""
    note: str | None = None


def synthetic_sha(file_id: str, text: str) -> str:
    """Drive gives no content hash and we do not hold the bytes, so identity is
    derived from the file id plus what we extracted. Re-importing an unchanged
    file is idempotent; an edited file gets a new row, which is correct — a
    revised contract is a different document."""
    h = hashlib.sha256()
    h.update(b"drive:"); h.update(file_id.encode()); h.update(b"\0")
    h.update(text.encode("utf-8", "replace"))
    return h.hexdigest()


def import_doc(doc: DriveDoc, cat: Catalog, *, run_id: int | None = None,
               now: int | None = None) -> dict:
    """Catalog one Drive file. Returns a small result dict."""
    digest = synthetic_sha(doc.file_id, doc.text)
    if cat.exists(digest):
        return {"status": "duplicate", "sha256": digest, "title": doc.title}

    # The sweep's classification wins when supplied — a human reviewed it.
    # Otherwise fall back to the same rules Gmail attachments go through.
    doc_type, tier = doc.doc_type, doc.tier
    if doc_type is None or tier is None:
        doc_type, tier = classify.classify(filename=doc.title, text=doc.text)

    art = Artifact(
        sha256=digest, bytes=0, mime=doc.mime, ext="",
        source="drive", source_ref=doc.file_id,
        original_name=doc.title,
        title=classify.title_for(filename=doc.title, text=doc.text),
        doc_type=doc_type, tier=int(tier),
        needs_review=0 if doc.text.strip() else 1,
        review_reason=None if doc.text.strip() else "no text extracted from Drive",
        tags=("drive",),
    )
    cat.upsert(art, now=now)
    if doc.text.strip():
        cat.set_text(digest, doc.text, extractor="drive-mcp", now=now)
        n = cat.add_fields(digest, fields.extract_fields(doc.text),
                           run_id=run_id, confidence=1.0)
    else:
        n = 0
    # The link is the retrieval path, since we hold no blob.
    cat.conn.execute(
        "INSERT OR IGNORE INTO artifact_fields (sha256,key,value,confidence,run_id) VALUES (?,?,?,?,?)",
        (digest, "drive_url", VIEW_URL.format(doc.file_id), 1.0, run_id))
    if doc.created:
        cat.conn.execute("UPDATE artifacts SET doc_date=coalesce(doc_date,?) WHERE sha256=?",
                         (doc.created, digest))
    cat.conn.commit()
    return {"status": "ok", "sha256": digest, "title": doc.title,
            "doc_type": doc_type, "tier": int(tier), "chars": len(doc.text), "fields": n}

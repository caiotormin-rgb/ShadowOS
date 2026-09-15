"""The pipeline: a file becomes a cataloged artifact.

Identical for every source. `source` is a column, not a code path -- a manual
drop and a Gmail harvest differ only in what they pass in.
"""
from __future__ import annotations

import mimetypes
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from . import classify, extract, fields, paths
from .store import Artifact, Catalog, sha256_file

# Attachments below this are almost always chrome: logos, signature images,
# tracking pixels. Measured on the pilot, where ~80% of attachments by count
# were decorative.
CHROME_MAX_BYTES = 40_000
CHROME_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".ico", ".svg"}


@dataclass
class Result:
    path: Path
    sha256: str | None = None
    status: str = "ok"          # ok | duplicate | skipped-chrome | failed
    doc_type: str | None = None
    tier: int | None = None
    chars: int = 0
    n_fields: int = 0
    note: str | None = None


def is_chrome(path: Path) -> bool:
    return (path.suffix.lower() in CHROME_SUFFIXES
            and path.stat().st_size <= CHROME_MAX_BYTES)


def consume_file(path: Path, cat: Catalog, *, source: str = "manual",
                 source_ref: str | None = None, thread_id: str | None = None,
                 run_id: int | None = None, keep_chrome: bool = False) -> Result:
    if not keep_chrome and is_chrome(path):
        return Result(path, status="skipped-chrome")

    digest = sha256_file(path)
    if cat.exists(digest):
        return Result(path, digest, "duplicate")

    got = extract.extract(path)
    doc_type, tier = classify.classify(filename=path.name, text=got.text)
    title = classify.title_for(filename=path.name, text=got.text)

    needs_review = 1 if got.chars == 0 else 0
    reason = None
    if needs_review:
        reason = got.error or "no text extracted"

    art = Artifact(
        sha256=digest, bytes=path.stat().st_size,
        mime=mimetypes.guess_type(path.name)[0], ext=path.suffix.lower(),
        source=source, source_ref=source_ref or str(path), thread_id=thread_id,
        original_name=path.name, title=title, doc_type=doc_type, tier=tier,
        needs_review=needs_review, review_reason=reason,
    )
    cat.upsert(art)
    cat.store_blob(path, digest)
    if got.chars:
        cat.set_text(digest, got.text, extractor=got.extractor, pages=got.pages)
    n = cat.add_fields(digest, fields.extract_fields(got.text), run_id=run_id,
                       confidence=1.0) if got.chars else 0
    return Result(path, digest, "ok", doc_type, tier, got.chars, n, reason)


def consume_dir(src: Path, cat: Catalog, *, source: str = "manual",
                move_done: bool = True, run_id: int | None = None) -> list[Result]:
    """Process every file under `src`. Consumed files are moved out of the way
    so a re-run is not a re-import; failures are moved to `rejected/` rather
    than left to be retried forever."""
    results: list[Result] = []
    for path in sorted(p for p in src.rglob("*") if p.is_file()):
        try:
            res = consume_file(path, cat, source=source, run_id=run_id)
        except Exception as e:                      # one bad file must not stop a batch
            res = Result(path, status="failed", note=f"{type(e).__name__}: {e}")
        results.append(res)
        if move_done:
            dest_root = paths.REJECTED if res.status == "failed" else None
            if dest_root:
                dest_root.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), dest_root / path.name)
            else:
                path.unlink()      # bytes are safe in the blob store
    return results

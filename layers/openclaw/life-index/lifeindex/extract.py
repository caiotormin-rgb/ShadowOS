"""Text extraction. Fast path first, fallback only when it comes back thin.

The pilot measured this: pdftotext handled 5 of 5 real documents cleanly,
including a 25-page school document (52k chars) and a 6-page contract. Docling exists for
scans and complex tables, and costs seconds per page, so it is not the
default -- it is what happens when the cheap path yields too little.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Below this many characters per page a PDF is treated as image-only. A
# scanned page usually yields a handful of stray ligatures, a digital one
# yields hundreds of characters.
THIN_CHARS_PER_PAGE = 120

PLAIN_SUFFIXES = {".txt", ".md", ".csv", ".log", ".json", ".eml"}


@dataclass
class Extracted:
    text: str
    pages: int | None
    extractor: str
    thin: bool = False
    error: str | None = None

    @property
    def chars(self) -> int:
        return len(self.text.strip())


def _run(cmd: list[str], timeout: int = 120) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, errors="replace")
        return p.returncode, p.stdout
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""


def pdf_pages(path: Path) -> int | None:
    code, out = _run(["pdfinfo", str(path)], timeout=30)
    if code != 0:
        return None
    for line in out.splitlines():
        if line.startswith("Pages:"):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return None
    return None


def extract_pdf(path: Path) -> Extracted:
    pages = pdf_pages(path)
    code, out = _run(["pdftotext", "-q", "-layout", str(path), "-"])
    if code == 127:
        return Extracted("", pages, "none", True, "pdftotext not installed")
    text = out.strip()
    per_page = len(text) / pages if pages else len(text)
    thin = per_page < THIN_CHARS_PER_PAGE
    return Extracted(text, pages, "pdftotext", thin)


def extract_docling(path: Path) -> Extracted:
    """Out-of-process so the catalog keeps no heavy import. Absent by design
    until the first scan needs it; absence is reported, never fatal."""
    exe = shutil.which("docling")
    if not exe:
        return Extracted("", None, "docling", True, "docling not installed")
    code, out = _run([exe, "--to", "md", "--output", "-", str(path)], timeout=900)
    if code != 0:
        return Extracted("", None, "docling", True, f"docling exit {code}")
    return Extracted(out.strip(), None, "docling", not out.strip())


def extract(path: Path) -> Extracted:
    suffix = path.suffix.lower()
    if suffix in PLAIN_SUFFIXES:
        try:
            text = path.read_text(errors="replace").strip()
        except OSError as e:
            return Extracted("", None, "plain", True, str(e))
        return Extracted(text, None, "plain", not text)
    if suffix == ".pdf":
        got = extract_pdf(path)
        if got.thin:
            better = extract_docling(path)
            # Keep whichever actually produced text; a missing Docling must
            # not throw away a partial pdftotext result.
            if better.chars > got.chars:
                return better
            got.error = got.error or better.error
        return got
    return extract_docling(path)

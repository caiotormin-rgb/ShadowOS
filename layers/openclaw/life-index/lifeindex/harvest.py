"""Import a Gmail harvest directory.

The harvest layout is one directory per message containing `meta.json`
(headers written by the fetcher), `body.txt` / `body.html`, and the
attachments. Walking it as a flat pile of files is wrong in three ways, all
of which happened on the first run:

  - `meta.json` was cataloged as a tier-1 contract, because it contains the
    subject line and the subject said COMPRA E VENDA. Sidecar metadata is
    not a document.
  - `body.html` produced eight zero-character artifacts flagged for review.
    It is the same content as body.txt with markup.
  - `body.txt` was typed from its own text, so an email *about* a contract
    became a tier-1 contract itself.

So the importer reads meta.json as metadata, skips body.html, files bodies as
tier-3 correspondence, and treats only real attachments as documents.
"""
from __future__ import annotations

import email.utils
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .consume import Result, consume_file, is_chrome
from .store import Catalog

SIDECAR = {"meta.json"}
BODY_TEXT = "body.txt"
SKIP = {"body.html", "_catalog.json", "manifest.json", "manifest.jsonl",
        "harvest.log", "harvest-state.sqlite"}

ADDR_RX = re.compile(r"<([^>]+)>")


@dataclass
class MessageMeta:
    message_id: str | None = None
    category: str | None = None
    sender: str | None = None
    subject: str | None = None
    date: str | None = None
    thread_id: str | None = None

    @property
    def sender_addr(self) -> str:
        if not self.sender:
            return ""
        m = ADDR_RX.search(self.sender)
        return (m.group(1) if m else self.sender).strip().strip('"').lower()

    @property
    def sender_name(self) -> str:
        """The display name, or the address when there is none. This is what
        the calibration report groups by, so it has to be stable."""
        if not self.sender:
            return ""
        name, addr = email.utils.parseaddr(self.sender)
        return (name or addr or "").strip().strip('"') or self.sender_addr

    @property
    def sender_domain(self) -> str:
        a = self.sender_addr
        return a.split("@", 1)[1] if "@" in a else ""

    @property
    def iso_date(self) -> str | None:
        """RFC-2822 header -> ISO date. A malformed Date: is common enough in
        real mail that it must not abort an import."""
        if not self.date:
            return None
        try:
            return email.utils.parsedate_to_datetime(self.date).date().isoformat()
        except (TypeError, ValueError):
            return None


def read_meta(d: Path) -> MessageMeta:
    try:
        raw = json.loads((d / "meta.json").read_text())
    except (OSError, ValueError):
        return MessageMeta()
    return MessageMeta(raw.get("message_id"), raw.get("category"),
                       raw.get("from"), raw.get("subject"), raw.get("date"),
                       raw.get("thread_id"))


def link_sender(cat: Catalog, meta: MessageMeta) -> int | None:
    """One `correspondents` row per sender.

    The corpus measurement is that SENDER is the template key that works --
    41 senders cover 50% of the non-marketing mail, 154 cover 80%, and only 2%
    of messages come from a sender seen once. Subject-derived keys fragment
    (82% singletons). So the sender is the unit the calibration report ranks
    and the unit an operator verdict attaches to, which means it has to be a
    first-class row rather than something re-parsed out of source_ref later.
    """
    addr = meta.sender_addr
    if not addr:
        return None
    name = meta.sender_name or addr
    cat.conn.execute(
        "INSERT OR IGNORE INTO correspondents (name, kind, primary_addr) VALUES (?,?,?)",
        (name, "org", addr))
    cat.conn.commit()
    row = cat.conn.execute(
        "SELECT id FROM correspondents WHERE name=? AND kind=?", (name, "org")).fetchone()
    return int(row["id"]) if row else None


def _provenance(cat: Catalog, digest: str, meta: MessageMeta,
                correspondent: int | None) -> None:
    """Attach the sender, the date, and the tags the calibration report groups
    by. Applied to attachments and bodies alike: a document's provenance is
    the message it arrived in, whichever part of it carried the bytes."""
    cat.conn.execute(
        """UPDATE artifacts
              SET thread_id = coalesce(thread_id, ?),
                  correspondent = coalesce(correspondent, ?),
                  doc_date = coalesce(doc_date, ?)
            WHERE sha256 = ?""",
        (meta.thread_id, correspondent, meta.iso_date, digest))
    tags = []
    if meta.sender_addr:
        tags.append(f"sender:{meta.sender_addr}")
    if meta.sender_domain:
        tags.append(f"domain:{meta.sender_domain}")
    if meta.iso_date:
        tags.append(f"year:{meta.iso_date[:4]}")
    for tag in tags:
        cat.conn.execute(
            "INSERT OR IGNORE INTO artifact_tags (sha256, tag) VALUES (?,?)",
            (digest, tag))
    cat.conn.commit()


def import_message(d: Path, cat: Catalog, *, run_id: int | None = None) -> list[Result]:
    meta = read_meta(d)
    ref = meta.message_id or d.name
    correspondent = link_sender(cat, meta)
    results: list[Result] = []

    attachments = [p for p in sorted(d.iterdir())
                   if p.is_file() and p.name not in SIDECAR
                   and p.name not in SKIP and p.name != BODY_TEXT]
    for p in attachments:
        res = consume_file(p, cat, source="gmail", source_ref=ref, run_id=run_id)
        if res.status == "ok" and res.sha256:
            _provenance(cat, res.sha256, meta, correspondent)
        results.append(res)

    body = d / BODY_TEXT
    if body.exists() and body.stat().st_size > 200:
        res = consume_file(body, cat, source="gmail", source_ref=ref, run_id=run_id)
        if res.status == "ok" and res.sha256:
            # Correspondence *about* a contract is not itself a contract.
            cat.conn.execute(
                """UPDATE artifacts
                      SET doc_type = 'correspondence', tier = 3,
                          title = coalesce(?, title), original_name = ?
                    WHERE sha256 = ?""",
                (meta.subject, f"{meta.subject or 'message'} (email body)", res.sha256))
            cat.conn.execute(
                "INSERT OR IGNORE INTO artifact_tags (sha256, tag) VALUES (?,?)",
                (res.sha256, "email-body"))
            if meta.category:
                cat.conn.execute(
                    "INSERT OR IGNORE INTO artifact_tags (sha256, tag) VALUES (?,?)",
                    (res.sha256, meta.category))
            cat.conn.commit()
            _provenance(cat, res.sha256, meta, correspondent)
            res.doc_type, res.tier = "correspondence", 3
        results.append(res)
    return results


def import_harvest(root: Path, cat: Catalog, *, run_id: int | None = None) -> list[Result]:
    out: list[Result] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if not (d / "meta.json").exists():
            continue
        out.extend(import_message(d, cat, run_id=run_id))
    return out

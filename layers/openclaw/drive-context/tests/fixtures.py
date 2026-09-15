"""Synthetic Drive payloads only. Real Drive metadata must never enter this
repository, and neither must a real file name, owner or link."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from drivectx.drive import FOLDER_MIME, SHORTCUT_MIME
from drivectx.sharing import Sharing
from drivectx.store import DriveFile

DAY = 86400
T0 = 1_750_000_000  # fixed epoch so tests are deterministic


def rfc3339(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def raw_file(file_id: str, *, name: str = "Notes", mime: str = "text/plain",
             parents: tuple[str, ...] = (), owners=None, permissions=None,
             drive_id: str | None = None, trashed: bool = False,
             explicitly_trashed: bool = False, starred: bool = False,
             created: int = T0, modified: int = T0, size: int | None = 1024,
             shortcut: tuple[str, str] | None = None, owned_by_me: bool = True,
             web_view_link: str = "https://drive.google.invalid/file/d/x/view") -> dict:
    """One files.list row, shaped the way Drive shapes it."""
    raw: dict = {
        "id": file_id,
        "name": name,
        "mimeType": mime,
        "createdTime": rfc3339(created),
        "modifiedTime": rfc3339(modified),
        "trashed": trashed,
        "explicitlyTrashed": explicitly_trashed,
        "starred": starred,
        "ownedByMe": owned_by_me,
        "webViewLink": web_view_link,
    }
    if parents:
        raw["parents"] = list(parents)
    if size is not None:
        raw["size"] = str(size)  # Drive sends size as a string
    if owners is None:
        owners = [("Caio", "caio@example.invalid")]
    raw["owners"] = [{"displayName": n, "emailAddress": e} for n, e in owners]
    if permissions is not None:
        raw["permissions"] = permissions
    if drive_id:
        raw["driveId"] = drive_id
    if shortcut:
        raw["shortcutDetails"] = {"targetId": shortcut[0], "targetMimeType": shortcut[1]}
    return raw


def raw_folder(file_id: str, *, name: str = "Folder", **kw) -> dict:
    kw.setdefault("size", None)
    return raw_file(file_id, name=name, mime=FOLDER_MIME, **kw)


def raw_shortcut(file_id: str, *, target: str = "target-id", **kw) -> dict:
    kw.setdefault("size", None)
    return raw_file(file_id, mime=SHORTCUT_MIME,
                    shortcut=(target, "text/plain"), **kw)


def change(file_id: str, *, removed: bool = False, file: dict | None = None,
           change_type: str = "file", drive_id: str | None = None) -> dict:
    rec: dict = {"fileId": file_id, "removed": removed, "changeType": change_type,
                 "time": rfc3339(T0)}
    if file is not None:
        rec["file"] = file
    if drive_id:
        rec["driveId"] = drive_id
    return rec


def dfile(file_id: str, *, name: str = "Notes", mime: str = "text/plain",
          parents: tuple[str, ...] = (), owners=(("Caio", "caio@example.invalid"),),
          sharing: Sharing | None = None, modified: int = T0, trashed: bool = False,
          **kw) -> DriveFile:
    """A store-level row, bypassing the parser."""
    kw.setdefault("created_ts", modified)
    return DriveFile(file_id=file_id, name=name, mime_type=mime, parents=parents,
                     owners=owners, sharing=sharing or Sharing.unknown(),
                     modified_ts=modified, trashed=trashed, **kw)


def perm(ptype: str, role: str = "reader", *, discoverable: bool = False,
         deleted: bool = False) -> dict:
    """One permission as the field mask delivers it: no email address, because
    drivectx.drive never asks for one."""
    p = {"type": ptype, "role": role}
    if ptype == "anyone":
        p["allowFileDiscovery"] = discoverable
    if deleted:
        p["deleted"] = True
    return p


def memory_conn() -> sqlite3.Connection:
    """A connection configured exactly like drivectx.store.connect().

    isolation_level=None matters: with Python's default, sqlite3 opens implicit
    transactions and an explicit BEGIN then fails. Tests that do not mirror
    production connection settings test the wrong thing.
    """
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

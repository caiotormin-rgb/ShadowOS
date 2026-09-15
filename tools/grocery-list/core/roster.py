"""Load an allowlist of people from a config file and apply it.

The roster is the single place a household is described: who may use the list,
what to call them, and which language to answer them in. Applying it is
idempotent, so it can be re-run after every edit.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import groups
import people
from errors import GroceryError


DEFAULT_ROSTER = Path(__file__).resolve().parent / "config" / "members.json"
PHONE = re.compile(r"^\+[1-9]\d{6,14}$")


def normalize_phone(value: str) -> str:
    """E.164, so one person is one identifier everywhere.

    The same number written +55 11 90000-0021 and +5511900000021 must not
    become two members with two histories.
    """
    cleaned = re.sub(r"[\s\-().]", "", str(value or ""))
    if not PHONE.match(cleaned):
        raise GroceryError(
            f"not a valid international number: {value!r} "
            "(expected E.164, e.g. +5511900000021)"
        )
    return cleaned


def load(path: str | Path = DEFAULT_ROSTER) -> dict[str, Any]:
    path = Path(path).expanduser()
    if not path.exists():
        raise GroceryError(f"no roster file at {path}")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise GroceryError(f"invalid roster JSON: {exc.msg} (line {exc.lineno})") from exc
    if not isinstance(data.get("members"), list) or not data["members"]:
        raise GroceryError("roster needs a non-empty 'members' list")

    seen: dict[str, str] = {}
    parsed = []
    for entry in data["members"]:
        if not isinstance(entry, dict):
            raise GroceryError("each member must be an object")
        phone = normalize_phone(entry.get("phone", ""))
        if phone in seen:
            raise GroceryError(f"{phone} is listed twice ({seen[phone]} and {entry.get('name')})")
        seen[phone] = str(entry.get("name", ""))
        role = entry.get("role", "member")
        if role not in groups.ROLES:
            raise GroceryError(f"unknown role for {phone}: {role}")
        lang = entry.get("lang", people.DEFAULT_LANG)
        if lang not in people.LANGS:
            raise GroceryError(f"unknown language for {phone}: {lang}")
        parsed.append({"phone": phone, "name": str(entry.get("name", "")).strip(),
                       "role": role, "lang": lang})
    return {"group": data.get("group") or None, "members": parsed}


def apply(
    conn: sqlite3.Connection, path: str | Path = DEFAULT_ROSTER, prune: bool = False
) -> dict[str, Any]:
    """Bring the database in line with the roster file.

    Revocation is deliberately opt-in. Silently dropping someone because a file
    was edited badly is the one change here that cannot be undone by re-running.
    """
    roster = load(path)
    group = groups.group_row(conn, roster["group"], create=True)

    for member in roster["members"]:
        groups.add_member(conn, group["name"], member["phone"], member["role"])
        people.remember_person(conn, member["phone"], member["lang"], member["name"])

    listed = {m["phone"] for m in roster["members"]}
    current = {m["actor"] for m in groups.members(conn, group["name"])}
    extra = sorted(current - listed)
    if extra and prune:
        for actor in extra:
            groups.remove_member(conn, group["name"], actor)

    return {
        "group": group["name"],
        "applied": [m["phone"] for m in roster["members"]],
        "not_in_file": [] if prune else extra,
        "revoked": extra if prune else [],
        "gateway_allow_from": [m["phone"] for m in roster["members"]],
    }

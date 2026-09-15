"""Request store: one SQLite file and a strict step machine per request.

A request only moves forward when the check for its current step passes, and
only its requester can see or change it. Nothing here talks to the network.

Steps: intake -> search (queued, then run in the background) -> shortlist -> summary -> closed
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path

import geo

DEFAULT_DB = Path(__file__).resolve().parent / "data" / "doctor.sqlite3"
STEPS = ["intake", "search", "shortlist", "summary", "closed"]
VISIT_TYPES = {"visit", "urgent", "lab"}
REQUIRED_INTAKE = ["patient", "zip", "visit_type", "specialty_text", "availability"]
# Only these intake fields exist. No member ID, date of birth or diagnosis:
# the insurance card contributes its plan name and nothing else.
INTAKE_FIELDS = {
    "patient": 120, "zip": 5, "miles": None, "max_miles": None,
    "availability": 200, "visit_type": 10, "specialty_text": 160,
    "context": 300, "cc": 80, "plan_name": 80, "language_pref": 40,
}
RETENTION_DAYS = 90
STALE_JOB_SECONDS = 3600
MAX_JOB_ATTEMPTS = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
  id TEXT PRIMARY KEY,
  requester TEXT NOT NULL,
  lang TEXT NOT NULL,
  step TEXT NOT NULL,
  intake TEXT NOT NULL DEFAULT '{}',
  codes TEXT NOT NULL DEFAULT '[]',
  search TEXT NOT NULL DEFAULT '{}',
  choice TEXT NOT NULL DEFAULT '[]',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  closed_at REAL
);
CREATE INDEX IF NOT EXISTS requests_requester ON requests(requester);
-- One row per shortlisted practice; `npi` holds the practice id (its web domain).
CREATE TABLE IF NOT EXISTS candidates (
  request_id TEXT NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
  rank INTEGER NOT NULL,
  npi TEXT NOT NULL,
  data TEXT NOT NULL,
  PRIMARY KEY (request_id, rank)
);
-- Audit trail: step changes and counts only, never intake text.
CREATE TABLE IF NOT EXISTS events (
  request_id TEXT NOT NULL,
  at REAL NOT NULL,
  action TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS cache (
  key TEXT PRIMARY KEY,
  fetched_at REAL NOT NULL,
  body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
-- Outreach (see outreach.py). One row per email to a practice.
CREATE TABLE IF NOT EXISTS outreach (
  id INTEGER PRIMARY KEY,
  request_id TEXT NOT NULL,
  rank INTEGER NOT NULL,
  to_addr TEXT NOT NULL,
  cc TEXT NOT NULL,
  reply_to TEXT NOT NULL,
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  body_hash TEXT NOT NULL,
  nonce TEXT NOT NULL,
  status TEXT NOT NULL,
  message_id TEXT,
  error TEXT,
  created_at REAL NOT NULL,
  sent_at REAL
);
CREATE INDEX IF NOT EXISTS outreach_request ON outreach(request_id);
CREATE INDEX IF NOT EXISTS outreach_message ON outreach(message_id);
CREATE TABLE IF NOT EXISTS inbound (
  id INTEGER PRIMARY KEY,
  request_id TEXT NOT NULL,
  uid INTEGER NOT NULL UNIQUE,
  from_addr TEXT NOT NULL,
  subject TEXT NOT NULL,
  text TEXT NOT NULL,
  received_at REAL NOT NULL,
  notified INTEGER NOT NULL DEFAULT 0,
  announced INTEGER NOT NULL DEFAULT 0
);
-- A requester's own email: contact data verified by a one-time code, never a credential.
CREATE TABLE IF NOT EXISTS contact_emails (
  requester TEXT PRIMARY KEY,
  email TEXT NOT NULL,
  code_hash TEXT,
  code_expires REAL,
  attempts INTEGER NOT NULL DEFAULT 0,
  verified_at REAL
);
CREATE TABLE IF NOT EXISTS suppression (
  address TEXT PRIMARY KEY,
  at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS mail_log (
  at REAL NOT NULL,
  kind TEXT NOT NULL,
  requester TEXT NOT NULL,
  request_id TEXT
);
"""


class DoctorError(Exception):
    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code = code
        self.extra = extra


def connect(path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    conn = sqlite3.connect(path, timeout=15)
    if fresh:
        os.chmod(path, 0o600)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def _log(conn, request_id: str, action: str, detail: str = "") -> None:
    conn.execute("INSERT INTO events VALUES (?,?,?,?)",
                 (request_id, time.time(), action, detail))


def _row(conn, request_id: str, requester: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
    # Someone else's request looks exactly like a missing one.
    if row is None or row["requester"] != requester:
        raise DoctorError("not_found", "No such request.")
    return row


def _require(row, step: str) -> None:
    if row["step"] != step:
        raise DoctorError("wrong_step", f"Request is at step '{row['step']}', not '{step}'.",
                          step=row["step"])


def _move(conn, row, step: str, **cols) -> None:
    sets = ", ".join(f"{k}=?" for k in cols)
    conn.execute(f"UPDATE requests SET step=?, updated_at=?{', ' + sets if sets else ''} WHERE id=?",
                 (step, time.time(), *cols.values(), row["id"]))
    _log(conn, row["id"], f"step:{step}")


def view(conn, request_id: str, requester: str) -> dict:
    row = _row(conn, request_id, requester)
    out = {k: row[k] for k in ("id", "lang", "step", "created_at", "updated_at", "closed_at")}
    for k in ("intake", "search", "choice"):
        out[k] = json.loads(row[k])
    out["missing"] = missing_intake(out["intake"])
    out["candidates"] = candidates(conn, request_id, requester)
    return out


def list_requests(conn, requester: str) -> list[dict]:
    rows = conn.execute("SELECT id, step, created_at, closed_at, intake, search FROM requests "
                        "WHERE requester=? ORDER BY created_at DESC", (requester,))
    return [{"id": r["id"], "step": r["step"], "created_at": r["created_at"],
             "closed_at": r["closed_at"], "search_status": json.loads(r["search"]).get("status"),
             "specialty_text": json.loads(r["intake"]).get("specialty_text")} for r in rows]


def new_request(conn, requester: str, lang: str) -> str:
    if not requester:
        raise DoctorError("no_requester", "Requester is required.")
    if lang not in ("en", "pt"):
        raise DoctorError("bad_lang", "lang must be en or pt.")
    request_id = "REQ-" + secrets.token_hex(3).upper()
    now = time.time()
    with conn:
        conn.execute("INSERT INTO requests (id, requester, lang, step, created_at, updated_at) "
                     "VALUES (?,?,?,?,?,?)", (request_id, requester, lang, "intake", now, now))
        _log(conn, request_id, "created")
    return request_id


def missing_intake(intake: dict) -> list[str]:
    return [f for f in REQUIRED_INTAKE if not intake.get(f)]


def _clean_intake(fields: dict) -> dict:
    out = {}
    for key, value in fields.items():
        if key not in INTAKE_FIELDS:
            raise DoctorError("bad_field", f"Unknown intake field '{key}'.", field=key)
        if key in ("miles", "max_miles"):
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise DoctorError("bad_field", f"{key} must be a number.", field=key)
            if not 1 <= value <= 60:
                raise DoctorError("bad_field", f"{key} must be between 1 and 60.", field=key)
        else:
            value = str(value).strip()
            if len(value) > INTAKE_FIELDS[key]:
                raise DoctorError("bad_field", f"{key} is too long.", field=key)
        if key == "zip" and not re.fullmatch(r"\d{5}", value):
            raise DoctorError("bad_field", "zip must be a 5-digit US ZIP code.", field=key)
        if key == "visit_type" and value not in VISIT_TYPES:
            raise DoctorError("bad_field", "visit_type must be visit, urgent or lab.", field=key)
        out[key] = value
    return out


def _with_radius(intake: dict) -> dict:
    intake.setdefault("miles", 10.0)
    if float(intake.get("max_miles", 0)) < float(intake["miles"]):
        intake["max_miles"] = min(max(float(intake["miles"]) * 2, 15.0), 60.0)
    return intake


def set_intake(conn, request_id: str, requester: str, fields: dict) -> dict:
    row = _row(conn, request_id, requester)
    _require(row, "intake")
    intake = _with_radius({**json.loads(row["intake"]), **_clean_intake(fields)})
    with conn:
        conn.execute("UPDATE requests SET intake=?, updated_at=? WHERE id=?",
                     (json.dumps(intake, ensure_ascii=False), time.time(), request_id))
        _log(conn, request_id, "intake", ",".join(sorted(fields)))
    return {"intake": intake, "missing": missing_intake(intake)}


def _queued() -> str:
    return json.dumps({"status": "queued", "queued_at": time.time(), "attempts": 0})


def confirm_intake(conn, request_id: str, requester: str) -> None:
    """Called only after the requester has seen and confirmed the intake summary.
    Queues the background search."""
    row = _row(conn, request_id, requester)
    _require(row, "intake")
    intake = json.loads(row["intake"])
    missing = missing_intake(intake)
    if missing:
        raise DoctorError("intake_incomplete", "Intake is missing fields.", missing=missing)
    if not geo.zip_known(intake["zip"]):
        raise DoctorError("bad_field", "Unknown ZIP code.", field="zip")
    with conn:
        _move(conn, row, "search", search=_queued())


def enqueue(conn, request_id: str, requester: str) -> None:
    """Run the search again, e.g. after it failed."""
    row = _row(conn, request_id, requester)
    _require(row, "search")
    if json.loads(row["search"]).get("status") in ("queued", "running"):
        raise DoctorError("already_running", "The search is already queued or running.")
    with conn:
        conn.execute("UPDATE requests SET search=?, updated_at=? WHERE id=?", (_queued(), time.time(), request_id))
        _log(conn, request_id, "job_queued")


def reopen_search(conn, request_id: str, requester: str, fields: dict) -> None:
    """Back from search or shortlist to a new search with a wider area."""
    row = _row(conn, request_id, requester)
    if row["step"] not in ("search", "shortlist"):
        raise DoctorError("wrong_step", "Only a search or shortlist can be widened.", step=row["step"])
    if json.loads(row["search"]).get("status") == "running":
        raise DoctorError("already_running", "The search is running; wait for it to finish.")
    allowed = {k: v for k, v in fields.items() if k in ("miles", "max_miles", "zip")}
    if set(fields) - set(allowed):
        raise DoctorError("bad_field", "Only zip, miles and max_miles can change here.")
    intake = {**json.loads(row["intake"]), **_clean_intake(allowed)}
    if "miles" in allowed and "max_miles" not in allowed:
        intake["max_miles"] = intake["miles"]
    intake = _with_radius(intake)
    with conn:
        conn.execute("DELETE FROM candidates WHERE request_id=?", (request_id,))
        _move(conn, row, "search", intake=json.dumps(intake, ensure_ascii=False), search=_queued())


def claim_job(conn) -> dict | None:
    """The next queued search (or one stuck for an hour, retried once), marked running."""
    now = time.time()
    with conn:
        row = conn.execute(
            "SELECT id, requester, lang, search FROM requests WHERE step='search' AND ("
            " json_extract(search, '$.status') = 'queued' OR"
            " (json_extract(search, '$.status') = 'running' AND json_extract(search, '$.started_at') < ?"
            "  AND COALESCE(json_extract(search, '$.attempts'), 0) < ?)) ORDER BY updated_at LIMIT 1",
            (now - STALE_JOB_SECONDS, MAX_JOB_ATTEMPTS)).fetchone()
        if row is None:
            return None
        meta = json.loads(row["search"])
        meta.update(status="running", started_at=now, attempts=int(meta.get("attempts", 0)) + 1)
        conn.execute("UPDATE requests SET search=?, updated_at=? WHERE id=?", (json.dumps(meta), now, row["id"]))
        _log(conn, row["id"], "job_started")
    return {"id": row["id"], "requester": row["requester"], "lang": row["lang"]}


def fail_job(conn, request_id: str, reason: str) -> None:
    row = conn.execute("SELECT search FROM requests WHERE id=?", (request_id,)).fetchone()
    if row is None:
        return
    meta = {**json.loads(row["search"]), "status": "failed", "reason": reason[:60], "finished_at": time.time()}
    with conn:
        conn.execute("UPDATE requests SET search=?, updated_at=? WHERE id=?", (json.dumps(meta), time.time(), request_id))
        _log(conn, request_id, "job_failed", reason[:60])


def save_search(conn, request_id: str, requester: str, results: list[dict], meta: dict) -> int:
    row = _row(conn, request_id, requester)
    _require(row, "search")
    with conn:
        conn.execute("DELETE FROM candidates WHERE request_id=?", (request_id,))
        for rank, cand in enumerate(results, 1):
            conn.execute("INSERT INTO candidates VALUES (?,?,?,?)",
                         (request_id, rank, cand["id"], json.dumps(cand, ensure_ascii=False)))
        _log(conn, request_id, "search", f"candidates={len(results)}")
        if any(c.get("verified") for c in results):
            _move(conn, row, "shortlist", search=json.dumps(meta))
        else:
            # Nothing found stays at search, so the agent offers a wider area.
            conn.execute("UPDATE requests SET search=?, updated_at=? WHERE id=?",
                         (json.dumps({**meta, "status": "empty"}), time.time(), request_id))
    return len(results)


def candidates(conn, request_id: str, requester: str) -> list[dict]:
    _row(conn, request_id, requester)
    rows = conn.execute("SELECT rank, data FROM candidates WHERE request_id=? ORDER BY rank",
                        (request_id,))
    return [{"rank": r["rank"], **json.loads(r["data"])} for r in rows]


def choose(conn, request_id: str, requester: str, ranks: list[int]) -> list[dict]:
    """Called with the requester's explicit pick from the shortlist."""
    row = _row(conn, request_id, requester)
    _require(row, "shortlist")
    by_rank = {c["rank"]: c for c in candidates(conn, request_id, requester)}
    ranks = list(dict.fromkeys(ranks))
    if not ranks or any(r not in by_rank for r in ranks):
        raise DoctorError("bad_choice", "Pick ranks from the shortlist.", valid=sorted(by_rank))
    if any(not by_rank[r].get("verified") for r in ranks):
        raise DoctorError("bad_choice", "Only checked providers can be chosen.")
    with conn:
        _move(conn, row, "summary", choice=json.dumps(ranks))
    return [by_rank[r] for r in ranks]


def close(conn, request_id: str, requester: str, reason: str = "done") -> None:
    row = _row(conn, request_id, requester)
    if row["step"] == "closed":
        return
    with conn:
        _move(conn, row, "closed", closed_at=time.time())
        _log(conn, request_id, "close", reason[:40])


def purge(conn, now: float | None = None) -> dict:
    """90 days after close, or after 90 days untouched, a request is deleted."""
    now = now or time.time()
    cutoff = now - RETENTION_DAYS * 86400
    with conn:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM requests WHERE (closed_at IS NOT NULL AND closed_at < ?) "
            "OR (closed_at IS NULL AND updated_at < ?)", (cutoff, cutoff))]
        for request_id in ids:
            for table in ("candidates", "outreach", "inbound", "events"):
                conn.execute(f"DELETE FROM {table} WHERE request_id=?", (request_id,))
            conn.execute("DELETE FROM requests WHERE id=?", (request_id,))
        conn.execute("DELETE FROM mail_log WHERE at < ?", (cutoff,))
        cache = conn.execute("DELETE FROM cache WHERE fetched_at < ?", (now - 7 * 86400,)).rowcount
    return {"requests": len(ids), "cache": cache}

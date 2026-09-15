#!/usr/bin/env python3
"""mailharvest — bulk, resumable Gmail attachment harvest.

The generalisation of `mailpilot.py`, which fetched 23 messages from a hand-
written TSV and had no memory. This one is built for thousands:

  resumable      per-message state in SQLite, committed as each message lands.
                 A run killed at message 900 of 3,000 restarts and does 2,100.
  rate-limited   reuses `mailctx.gmail`'s classification -- Gmail reports
                 per-user rate limiting as HTTP 403 with reason
                 rateLimitExceeded, NOT 429 -- and backs off exponentially,
                 then recovers, so one early burst does not cripple the run.
  bounded        a shared token-bucket across a small thread pool, so
                 concurrency never raises the request rate above the ceiling.
  ordered        the fetch list is best-first, so a run stopped early has
                 already brought back the messages most likely to matter.
  accountable    manifest.jsonl (one row per message) + manifest.json
                 (the run summary) + harvest.log.

Read-only throughout: the only Gmail calls are messages.get(format=full) and
messages.attachments.get, both already on the transport allowlist. There is no
send path here and there must never be one.

Output is PLAINTEXT PII -- bodies, contracts, identity documents. It is meant
to be imported into the life-index catalog and then shredded.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

# Chrome: logos, signature graphics, tracking pixels. ~80% of pilot
# attachments by count. format=full reports `body.size` on every part, so this
# decision is made BEFORE spending an attachments.get call on it -- the point
# of filtering at fetch time rather than at import time.
CHROME_MAX_BYTES = 40_000
CHROME_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".ico", ".svg", ".tif", ".tiff"}
DEFAULT_MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

# Errors worth trying again inside a run. `auth`, `forbidden` and `not_found`
# are not: retrying a revoked token or a deleted message just burns the clock.
RETRYABLE = {"rate_limited", "network", "http_error"}

# The message is gone. No number of runs will change that, so record it and
# never spend another call on it.
PERMANENT = {"not_found"}

# Not a property of the message at all -- the token died or the scope is wrong,
# and every remaining message would fail identically. Abort the run and record
# NOTHING for the message that tripped it. The alternative is worse than a
# crash: 2,100 messages marked 'failed' by a token that expired at message 900,
# then skipped forever by the resume logic that was supposed to save them.
ABORT = {"auth", "forbidden"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS harvest_messages (
  message_id  TEXT PRIMARY KEY,
  status      TEXT NOT NULL,          -- ok | failed | skipped
  attempts    INTEGER NOT NULL DEFAULT 0,   -- cumulative tries, informational
  runs        INTEGER NOT NULL DEFAULT 0,   -- how many RUNS have given up on it
  attachments INTEGER NOT NULL DEFAULT 0,
  skipped     INTEGER NOT NULL DEFAULT 0,
  bytes       INTEGER NOT NULL DEFAULT 0,
  error_class TEXT,
  updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS harvest_messages_status ON harvest_messages(status);

CREATE TABLE IF NOT EXISTS harvest_runs (
  run_id     INTEGER PRIMARY KEY,
  started_at INTEGER NOT NULL,
  finished_at INTEGER,
  requested  INTEGER NOT NULL DEFAULT 0,
  resumed    INTEGER NOT NULL DEFAULT 0,
  fetched    INTEGER NOT NULL DEFAULT 0,
  failed     INTEGER NOT NULL DEFAULT 0,
  bytes      INTEGER NOT NULL DEFAULT 0,
  status     TEXT NOT NULL DEFAULT 'running',
  note       TEXT
);
"""


# -- state --------------------------------------------------------------

class HarvestState:
    """Per-message durable progress.

    Only the main thread touches this: workers return results and the loop
    records them. That keeps SQLite single-threaded without a lock, and means
    a row exists only once the bytes are actually on disk.
    """

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def done_ids(self, *, max_runs: int = 3) -> set[str]:
        """Ids a resumed run must not fetch again.

        Two separate budgets, because they answer different questions.
        `max_attempts` is how hard to try *inside* one run, against a condition
        that might clear in seconds (a rate limit). `max_runs` is how many
        times a human has re-launched the harvest and this message still failed
        -- and between runs the world genuinely changes, so a network failure
        deserves the next run even though it burned every attempt in this one.
        """
        marks = ",".join("?" * len(PERMANENT))
        rows = self.conn.execute(
            f"""SELECT message_id FROM harvest_messages
                 WHERE status IN ('ok','skipped')
                    OR (status = 'failed' AND (runs >= ? OR error_class IN ({marks})))""",
            (max_runs, *sorted(PERMANENT)))
        return {r["message_id"] for r in rows}

    def attempts(self, message_id: str) -> int:
        row = self.conn.execute(
            "SELECT attempts FROM harvest_messages WHERE message_id=?",
            (message_id,)).fetchone()
        return row["attempts"] if row else 0

    def record(self, res: "MessageResult", *, now: int | None = None) -> None:
        failed = 1 if res.status == "failed" else 0
        self.conn.execute(
            """INSERT INTO harvest_messages (message_id, status, attempts, runs,
                   attachments, skipped, bytes, error_class, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(message_id) DO UPDATE SET
                   status = excluded.status,
                   attempts = harvest_messages.attempts + excluded.attempts,
                   runs = harvest_messages.runs + excluded.runs,
                   attachments = excluded.attachments,
                   skipped = excluded.skipped,
                   bytes = excluded.bytes,
                   error_class = excluded.error_class,
                   updated_at = excluded.updated_at""",
            (res.message_id, res.status, max(1, res.attempts), failed,
             len(res.attachments), len(res.skipped), res.bytes, res.error_class,
             now if now is not None else int(time.time())))
        self.conn.commit()

    def start_run(self, requested: int, resumed: int, now: int | None = None) -> int:
        cur = self.conn.execute(
            """INSERT INTO harvest_runs (started_at, requested, resumed)
               VALUES (?,?,?)""",
            (now if now is not None else int(time.time()), requested, resumed))
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, *, fetched: int, failed: int, bytes_: int,
                   status: str = "done", note: str | None = None) -> None:
        self.conn.execute(
            """UPDATE harvest_runs SET finished_at=?, fetched=?, failed=?, bytes=?,
                   status=?, note=? WHERE run_id=?""",
            (int(time.time()), fetched, failed, bytes_, status, note, run_id))
        self.conn.commit()

    def totals(self) -> dict:
        row = self.conn.execute(
            """SELECT count(*) n,
                      sum(status='ok') ok,
                      sum(status='failed') failed,
                      coalesce(sum(bytes),0) bytes,
                      coalesce(sum(attachments),0) atts,
                      coalesce(sum(skipped),0) skipped
                 FROM harvest_messages""").fetchone()
        return {k: (row[k] or 0) for k in ("n", "ok", "failed", "bytes", "atts", "skipped")}


# -- results ------------------------------------------------------------

@dataclass
class MessageResult:
    message_id: str
    category: str = ""
    status: str = "ok"                       # ok | failed | skipped
    dir: str | None = None
    attempts: int = 1
    bytes: int = 0
    error_class: str | None = None
    attachments: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    body_chars: int = 0
    sender: str | None = None
    date: str | None = None

    def manifest_row(self, *, subjects: bool = True, subject: str | None = None) -> dict:
        row = {
            "message_id": self.message_id, "category": self.category,
            "status": self.status, "dir": self.dir, "bytes": self.bytes,
            "attempts": self.attempts, "error_class": self.error_class,
            "sender": self.sender, "date": self.date,
            "body_chars": self.body_chars,
            "attachments": self.attachments, "skipped": self.skipped,
        }
        if subjects and subject is not None:
            row["subject"] = subject
        return row


# -- fetching -----------------------------------------------------------

def b64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def safe_name(name: str, fallback: str) -> str:
    name = re.sub(r"[^\w.\- ()\[\]]", "_", name or "").strip() or fallback
    return name[:120]


def is_chrome(filename: str, size: int) -> bool:
    return Path(filename).suffix.lower() in CHROME_SUFFIXES and size <= CHROME_MAX_BYTES


@dataclass
class FetchOptions:
    skip_chrome: bool = True
    max_attachment_bytes: int = DEFAULT_MAX_ATTACHMENT_BYTES
    keep_html: bool = False


def collect_parts(payload: dict) -> tuple[list[dict], list[dict]]:
    """Flatten the MIME tree into (attachments, text bodies).

    Returned attachment descriptors carry `size` from `body.size`, which
    format=full always provides, so a chrome or oversize decision is made
    without downloading anything.
    """
    atts: list[dict] = []
    bodies: list[dict] = []

    def walk(part: dict) -> None:
        mime = part.get("mimeType", "") or ""
        body = part.get("body", {}) or {}
        fname = part.get("filename") or ""
        if fname:
            atts.append({
                "filename": fname, "mime": mime,
                "size": int(body.get("size") or 0),
                "attachment_id": body.get("attachmentId"),
                "data": body.get("data"),
            })
        elif mime in ("text/plain", "text/html") and body.get("data"):
            bodies.append({"mime": mime, "data": body["data"]})
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload or {})
    return atts, bodies


def fetch_message(api, message_id: str, out_dir: Path, *, category: str = "",
                  opts: FetchOptions | None = None) -> MessageResult:
    """One message: headers, bodies, attachments. Writes into `out_dir`."""
    opts = opts or FetchOptions()
    res = MessageResult(message_id, category)
    msg = api.message_full(message_id)
    payload = msg.get("payload", {}) or {}
    headers = {h["name"].lower(): h["value"]
               for h in payload.get("headers", []) or []}
    out_dir.mkdir(parents=True, exist_ok=True)
    res.dir = str(out_dir)
    res.sender = headers.get("from")
    res.date = headers.get("date")

    (out_dir / "meta.json").write_text(json.dumps({
        "message_id": message_id, "thread_id": msg.get("threadId"),
        "category": category, "from": headers.get("from"),
        "to": headers.get("to"), "cc": headers.get("cc"),
        "date": headers.get("date"), "subject": headers.get("subject"),
        "label_ids": msg.get("labelIds", []),
        "internal_date": msg.get("internalDate"),
    }, indent=1, ensure_ascii=False))

    atts, bodies = collect_parts(payload)

    text = b"".join(b64(b["data"]) + b"\n" for b in bodies if b["mime"] == "text/plain")
    if text:
        (out_dir / "body.txt").write_bytes(text)
        res.body_chars = len(text)
    if opts.keep_html:
        html = b"".join(b64(b["data"]) for b in bodies if b["mime"] == "text/html")
        if html:
            (out_dir / "body.html").write_bytes(html)

    for i, att in enumerate(atts):
        name = safe_name(att["filename"], f"attachment-{i}")
        size = att["size"]
        if opts.skip_chrome and is_chrome(name, size):
            res.skipped.append({"filename": name, "mime": att["mime"],
                                "size": size, "reason": "chrome"})
            continue
        if size > opts.max_attachment_bytes:
            res.skipped.append({"filename": name, "mime": att["mime"],
                                "size": size, "reason": "oversize"})
            continue
        if att["data"]:
            data = b64(att["data"])
        elif att["attachment_id"]:
            data = b64(api.attachment(message_id, att["attachment_id"])["data"])
        else:
            res.skipped.append({"filename": name, "mime": att["mime"],
                                "size": size, "reason": "no-data"})
            continue
        # Collisions are real: two parts can both be called "scan.pdf".
        dest = out_dir / name
        n = 1
        while dest.exists():
            dest = out_dir / f"{Path(name).stem}-{n}{Path(name).suffix}"
            n += 1
        dest.write_bytes(data)
        res.bytes += len(data)
        res.attachments.append({
            "filename": dest.name, "mime": att["mime"], "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    return res


def with_retry(fn: Callable[[], MessageResult], *, max_attempts: int = 3,
               base_delay: float = 2.0, max_delay: float = 60.0,
               sleep: Callable[[float], None] = time.sleep,
               rand: Callable[[], float] = random.random,
               on_retry: Callable[[int, str, float], None] | None = None) -> MessageResult:
    """Exponential backoff with jitter, for retryable transport classes only.

    Jitter matters with a thread pool: without it, N workers that all hit the
    same rate limit wake at the same instant and hit it again together.
    """
    from mailctx.gmail import TransportError

    attempt = 0
    last: Exception | None = None
    while attempt < max_attempts:
        attempt += 1
        try:
            res = fn()
            res.attempts = attempt
            return res
        except TransportError as exc:
            last = exc
            if exc.error_class not in RETRYABLE or attempt >= max_attempts:
                break
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay) * (0.5 + rand())
            if on_retry:
                on_retry(attempt, exc.error_class, delay)
            sleep(delay)
        except Exception as exc:                 # a bad message must not kill a run
            last = exc
            break
    cls = getattr(last, "error_class", None) or type(last).__name__
    return MessageResult("", status="failed", attempts=attempt, error_class=cls)


# -- the run ------------------------------------------------------------

def read_candidates(path: Path) -> list[dict]:
    """Parse the fetch list by header name, not by column position.

    `mailpilot`'s positional parse is why the pilot TSV could never grow a
    column. The first two columns stay `category`, `message_id` so an old file
    still reads.
    """
    with open(path, newline="") as f:
        rows = list(csv.reader(f, delimiter="\t"))
    if not rows:
        return []
    header = [h.strip() for h in rows[0]]
    if "message_id" not in header:
        header = ["category", "message_id"] + header[2:]
        rows = [header] + rows
    idx = {h: i for i, h in enumerate(header)}
    out = []
    for r in rows[1:]:
        if not r or not r[idx["message_id"]].strip():
            continue
        out.append({h: (r[i] if i < len(r) else "") for h, i in idx.items()})
    return out


def _api_factory(shared_limiter, opener=None, tokens=None):
    """One transport per worker thread, one rate limiter for all of them.

    A shared limiter is what makes concurrency safe: the token bucket is the
    global ceiling, so raising --workers raises parallelism, never the request
    rate. A shared *transport* would instead race on its call counters.
    `AccessTokenProvider.bearer()` already takes a lock, so one provider across
    all workers is correct and avoids N concurrent refreshes.
    """
    from mailctx.auth import AccessTokenProvider
    from mailctx.gmail import GmailReadOnly

    tokens = tokens if tokens is not None else AccessTokenProvider()
    local = threading.local()

    def get():
        if not hasattr(local, "api"):
            kw = {"rate_limiter": shared_limiter}
            if opener is not None:
                kw["opener"] = opener
            local.api = GmailReadOnly(tokens, **kw)
        return local.api
    return get


def run_harvest(candidates: list[dict], out_root: Path, *, api_for,
                state: HarvestState, opts: FetchOptions | None = None,
                workers: int = 4, max_attempts: int = 3, max_runs: int = 3,
                progress_every: int = 25, log=sys.stderr,
                limiter=None, recover_after: int = 50,
                sleep: Callable[[float], None] = time.sleep,
                subjects_in_manifest: bool = True) -> dict:
    opts = opts or FetchOptions()
    out_root.mkdir(parents=True, exist_ok=True)

    done = state.done_ids(max_runs=max_runs)
    todo = [c for c in candidates if c["message_id"] not in done]
    run_id = state.start_run(len(candidates), len(candidates) - len(todo))
    print(f"harvest run {run_id}: {len(candidates)} candidates, "
          f"{len(candidates) - len(todo)} already done, {len(todo)} to fetch",
          file=log, flush=True)

    manifest = out_root / "manifest.jsonl"
    started = time.monotonic()
    fetched = failed = 0
    total_bytes = 0
    consecutive_ok = 0

    def work(cand: dict) -> tuple[dict, MessageResult]:
        mid = cand["message_id"]
        cat = cand.get("category") or cand.get("sender_class") or "uncategorized"
        d = out_root / f"{safe_name(cat, 'x')}-{safe_name(mid, 'msg')}"
        api = api_for()
        res = with_retry(
            lambda: fetch_message(api, mid, d, category=cat, opts=opts),
            max_attempts=max_attempts, sleep=sleep,
            on_retry=lambda n, cls, delay: print(
                f"retry {n}/{max_attempts} {mid} {cls} in {delay:.1f}s",
                file=log, flush=True))
        res.message_id, res.category = mid, cat
        return cand, res

    aborted: str | None = None
    with open(manifest, "a") as mf:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = [pool.submit(work, c) for c in todo]
            for i, fut in enumerate(as_completed(futures), 1):
                cand, res = fut.result()
                if res.status == "failed" and res.error_class in ABORT:
                    # Do NOT record it: the message is fine, the session is not.
                    aborted = res.error_class
                    print(f"ABORT: {res.error_class} — stopping the run so the "
                          f"remaining {len(todo) - i} messages stay retryable",
                          file=log, flush=True)
                    for f in futures:
                        f.cancel()
                    break
                state.record(res)
                mf.write(json.dumps(res.manifest_row(
                    subjects=subjects_in_manifest,
                    subject=cand.get("subject")), ensure_ascii=False) + "\n")
                mf.flush()
                if res.status == "ok":
                    fetched += 1
                    total_bytes += res.bytes
                    consecutive_ok += 1
                    # Recover the pacing the transport widened on a 403. Without
                    # this, one early burst slows the whole remaining run.
                    if limiter is not None and consecutive_ok % recover_after == 0:
                        recover(limiter)
                else:
                    failed += 1
                    consecutive_ok = 0
                    print(f"FAIL {res.message_id} {res.error_class}", file=log, flush=True)
                if progress_every and i % progress_every == 0:
                    el = max(1e-6, time.monotonic() - started)
                    rate = i / el
                    left = (len(todo) - i) / rate if rate else 0
                    print(f"  {i}/{len(todo)}  ok={fetched} fail={failed} "
                          f"{total_bytes / 1e6:.1f}MB  {rate:.2f} msg/s  "
                          f"eta {left / 60:.1f}min", file=log, flush=True)

    status = ("aborted" if aborted else "done" if failed == 0 else "done-with-errors")
    state.finish_run(run_id, fetched=fetched, failed=failed, bytes_=total_bytes,
                     status=status, note=aborted)
    t = state.totals()
    summary = {
        "run_id": run_id, "out": str(out_root), "status": status,
        "aborted_because": aborted,
        "candidates": len(candidates), "attempted": len(todo),
        "fetched_this_run": fetched, "failed_this_run": failed,
        "bytes_this_run": total_bytes,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "cumulative": t,
    }
    (out_root / "manifest.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1), file=log, flush=True)
    return summary


def recover(limiter, factor: float = 0.8) -> None:
    """Narrow the interval back toward what the caller asked for.

    `RateLimiter.back_off` only ever widens and caps at one request per second;
    on a run of thousands that is a permanent tax for a transient burst.
    """
    with limiter._lock:
        limiter.min_interval = max(getattr(limiter, "floor_interval", 0.0),
                                   limiter.min_interval * factor)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mailharvest")
    ap.add_argument("--ids", required=True, type=Path,
                    help="fetch list TSV from mailctx.selectrun")
    ap.add_argument("--out", type=Path, default=Path("/tmp/mailharvest"))
    ap.add_argument("--state", type=Path,
                    help="resume DB (default: <out>/harvest-state.sqlite)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--rate", type=float, default=10.0,
                    help="requests/second ceiling across all workers")
    ap.add_argument("--max-attempts", type=int, default=3,
                    help="tries per message inside one run (backoff between)")
    ap.add_argument("--max-runs", type=int, default=3,
                    help="how many runs may fail a message before it is given up on")
    ap.add_argument("--limit", type=int, help="fetch at most N (best-first)")
    ap.add_argument("--keep-chrome", action="store_true",
                    help="download logos and signature graphics too")
    ap.add_argument("--keep-html", action="store_true")
    ap.add_argument("--max-attachment-bytes", type=int,
                    default=DEFAULT_MAX_ATTACHMENT_BYTES)
    ap.add_argument("--no-subjects", action="store_true",
                    help="omit subjects from the manifest")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be fetched; make no API call")
    a = ap.parse_args(argv)

    cands = read_candidates(a.ids)
    if a.limit:
        cands = cands[:a.limit]
    state = HarvestState(a.state or (a.out / "harvest-state.sqlite"))
    if a.dry_run:
        done = state.done_ids(max_runs=a.max_runs)
        todo = [c for c in cands if c["message_id"] not in done]
        print(json.dumps({"candidates": len(cands), "already_done": len(cands) - len(todo),
                          "would_fetch": len(todo),
                          "estimated_api_calls_min": len(todo),
                          "estimated_api_calls_max": len(todo) * 3,
                          "state_db": str(state.path)}, indent=1))
        return 0

    from mailctx.gmail import RateLimiter
    limiter = RateLimiter(per_second=a.rate)
    api_for = _api_factory(limiter)
    opts = FetchOptions(skip_chrome=not a.keep_chrome,
                        max_attachment_bytes=a.max_attachment_bytes,
                        keep_html=a.keep_html)
    logf = a.out / "harvest.log"
    a.out.mkdir(parents=True, exist_ok=True)
    with open(logf, "a") as lf:
        class Tee:
            def write(self, s): sys.stderr.write(s); lf.write(s)
            def flush(self): sys.stderr.flush(); lf.flush()
        summary = run_harvest(cands, a.out, api_for=api_for, state=state, opts=opts,
                              workers=a.workers, max_attempts=a.max_attempts,
                              max_runs=a.max_runs, log=Tee(), limiter=limiter,
                              subjects_in_manifest=not a.no_subjects)
    try:
        os.chmod(a.out, 0o700)
    except OSError:
        pass
    if summary["status"] == "aborted":
        return 2
    return 0 if summary["failed_this_run"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

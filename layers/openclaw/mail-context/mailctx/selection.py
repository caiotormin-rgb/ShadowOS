"""Layers 1 and 2 of the parsing cascade: which messages get fetched.

Numbering follows the master plan's table. Layer 0 is the Gmail listing query
itself (`-category:promotions`), which `mailctx.syncrun --exclude` already
applies so those ids never enter the index. This module is what happens next:

    layer 1  phase-0 sender class == 'marketing'   kills 43,950 of 64,200 (68.5%)
    layer 1  operator sender rule verdict 'never'  kills whatever it covers
    layer 2  no document-shaped attachment hint    kills the rest of the noise

and what survives is the fetch list. The cascade rule is that every layer may
only TERMINATE or EXTRACT, never merely annotate: a message killed here costs
nothing downstream -- no API call, no bytes, no model call.

The key insight the numbers force: **the template key is the sender, not the
subject.** Sender-only gives 1,163 families over the non-marketing corpus, 41
senders cover 50% of it, 154 cover 80%, and only 2% of messages come from a
sender seen once. Subject-derived keys fragment -- 82% singletons -- so a
decision made about a subject buys almost nothing, while a decision made about
a sender applies forever. `sender_rules` is where those decisions persist, and
`sender_census()` is what ranks them so the operator spends 154 decisions
rather than 20,250.
"""
from __future__ import annotations

import csv
import re
import time
from pathlib import Path
from typing import Iterable, Sequence

from .targeting import DOCUMENT_HINTS

ADDR_RX = re.compile(r"<([^>]+)>")

# Phase-0 classes that are not worth fetching on the wide first pass. Only
# marketing: 'unknown' is 13.6% of the corpus and is exactly the population we
# have no judgement about yet, so excluding it would defeat the purpose of a
# calibration run.
DEFAULT_EXCLUDE_CLASSES = ("marketing",)

# Ranking weights. Rank does not decide *whether* a message is fetched on this
# pass -- everything selected is fetched. It decides the ORDER, so that a run
# stopped early (rate limits, an interrupted operator session, a disk filling)
# has already brought back the messages most likely to matter.
CLASS_SCORE = {"human": 4, "service-transactional": 2,
               "service-notification": 1, "unknown": 1, "marketing": 0}
LABEL_SCORE = {"IMPORTANT": 3, "CATEGORY_PERSONAL": 2, "SENT": 2, "STARRED": 3,
               "INBOX": 1}
HINT_SCORE = {"pdf": 2, "doc": 2, "sheet": 1, "large": 2}


def normalize_addr(raw: str | None) -> str:
    """`"Name" <a@b.com>` -> `a@b.com`. Lowercased, angle brackets stripped."""
    if not raw:
        return ""
    m = ADDR_RX.search(raw)
    return (m.group(1) if m else raw).strip().strip('"').strip("'").lower()


def domain_of(addr: str) -> str:
    return "@" + addr.split("@", 1)[1] if "@" in addr else ""


# -- inputs -------------------------------------------------------------

def load_sender_classes(path: Path | str) -> dict[str, str]:
    """Read the phase-0 classification (`senders.csv`, address-keyed)."""
    out: dict[str, str] = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            addr = normalize_addr(row.get("sender"))
            if addr:
                out[addr] = (row.get("class") or "unknown").strip()
    return out


def load_verdicts(path: Path | str) -> dict[str, str]:
    """Read operator sender verdicts (`sender-verdicts.csv`).

    Columns: sender, verdict[, reason]. `sender` may be an address or
    `@domain`. Blank or `review` verdicts are dropped -- an undecided row is
    not a decision, and keeping it would make "reviewed and kept" and "never
    looked at" indistinguishable.
    """
    out: dict[str, str] = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            key = (row.get("sender") or "").strip().lower()
            verdict = (row.get("verdict") or "").strip().lower()
            if key and verdict in ("never", "always"):
                out[key] = verdict
    return out


def store_verdicts(conn, verdicts: dict[str, str], *, reasons: dict[str, str] | None = None,
                   decided_by: str = "operator", now: int | None = None) -> int:
    """Persist verdicts into `sender_rules` so they survive the CSV."""
    now = int(time.time()) if now is None else now
    reasons = reasons or {}
    n = 0
    for sender, verdict in verdicts.items():
        conn.execute(
            """INSERT INTO sender_rules (sender, verdict, reason, decided_by, decided_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(sender) DO UPDATE SET
                   verdict = excluded.verdict, reason = excluded.reason,
                   decided_by = excluded.decided_by, decided_at = excluded.decided_at""",
            (sender, verdict, reasons.get(sender), decided_by, now))
        n += 1
    conn.commit()
    return n


def load_rules(conn) -> dict[str, str]:
    """Verdicts already stored in the DB, address and @domain keys together."""
    try:
        rows = conn.execute(
            "SELECT sender, verdict FROM sender_rules WHERE verdict IN ('never','always')")
    except Exception:
        return {}
    return {r["sender"]: r["verdict"] for r in rows}


def verdict_for(addr: str, verdicts: dict[str, str]) -> str | None:
    """Address rules beat domain rules: a `@bank.com -> never` blanket must not
    silence the one human at that bank whom Caio explicitly kept."""
    if addr in verdicts:
        return verdicts[addr]
    return verdicts.get(domain_of(addr))


# -- the cascade --------------------------------------------------------

def _label_map(conn, ids: Sequence[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(
                f"SELECT message_id, label FROM mail_labels WHERE message_id IN ({marks})",
                chunk):
            out.setdefault(r["message_id"], set()).add(r["label"])
    return out


def score_row(sender_class: str, labels: set[str], hints: set[str]) -> int:
    return (CLASS_SCORE.get(sender_class, 1)
            + sum(LABEL_SCORE.get(l, 0) for l in labels)
            + sum(HINT_SCORE.get(h, 0) for h in hints))


def select_candidates(conn, *, classes: dict[str, str] | None = None,
                      verdicts: dict[str, str] | None = None,
                      exclude_classes: Iterable[str] = DEFAULT_EXCLUDE_CLASSES,
                      hints: Iterable[str] = DOCUMENT_HINTS,
                      since_ts: int | None = None, until_ts: int | None = None,
                      limit: int | None = None) -> list[dict]:
    """The fetch list, best-first.

    Deliberately WIDE for the calibration pass: every message carrying a
    document-shaped attachment from a sender that layer 0 did not kill, across
    the whole indexed window. No subject filtering, no date narrowing, no
    doc_type guessing -- the point is to see what comes back.
    """
    classes = classes or {}
    verdicts = verdicts or {}
    excluded = set(exclude_classes)
    hint_list = tuple(hints)
    marks = ",".join("?" * len(hint_list))
    params: list = [*hint_list]
    where = [f"h.hint IN ({marks})", "m.deleted_at IS NULL"]
    if since_ts is not None:
        where.append("m.internal_ts >= ?"); params.append(since_ts)
    if until_ts is not None:
        where.append("m.internal_ts <= ?"); params.append(until_ts)
    rows = conn.execute(
        f"""SELECT m.message_id, m.thread_id, m.internal_ts, m.from_addr, m.subject,
                   group_concat(DISTINCT h.hint) AS hints
              FROM mail_attachment_hints h
              JOIN mail_messages m ON m.message_id = h.message_id
             WHERE {' AND '.join(where)}
             GROUP BY m.message_id""", params).fetchall()

    kept: list[dict] = []
    for r in rows:
        addr = normalize_addr(r["from_addr"])
        cls = classes.get(addr, "unknown")
        v = verdict_for(addr, verdicts)
        if v == "never":                        # layer 1: operator termination
            continue
        if v != "always" and cls in excluded:   # layer 1: phase-0 class
            continue
        kept.append({
            "message_id": r["message_id"], "thread_id": r["thread_id"],
            "internal_ts": r["internal_ts"], "sender": addr,
            "from_addr": r["from_addr"], "subject": r["subject"],
            "sender_class": cls, "verdict": v or "",
            "hints": sorted((r["hints"] or "").split(",")) if r["hints"] else [],
        })

    labels = _label_map(conn, [k["message_id"] for k in kept])
    for k in kept:
        lab = labels.get(k["message_id"], set())
        k["labels"] = sorted(lab)
        k["score"] = score_row(k["sender_class"], lab, set(k["hints"]))
    kept.sort(key=lambda k: (-k["score"], -k["internal_ts"]))
    return kept[:limit] if limit else kept


# -- the sender census, which is what the operator actually acts on -----

def sender_census(conn, *, classes: dict[str, str] | None = None,
                  verdicts: dict[str, str] | None = None,
                  exclude_classes: Iterable[str] = (),
                  hints: Iterable[str] = DOCUMENT_HINTS,
                  live_only: bool = True) -> list[dict]:
    """One row per sender: how much of the corpus it is, and how much of the
    fetch list it is. Ranked by message count, with a running coverage
    percentage -- the cost curve that says when to stop deciding.
    """
    classes = classes or {}
    verdicts = verdicts or {}
    excluded = set(exclude_classes)
    hint_list = tuple(hints)
    marks = ",".join("?" * len(hint_list))

    live = "WHERE deleted_at IS NULL" if live_only else ""
    agg: dict[str, dict] = {}
    for r in conn.execute(
            f"""SELECT from_addr, count(*) n, max(internal_ts) last_ts,
                       min(internal_ts) first_ts
                  FROM mail_messages {live} GROUP BY from_addr"""):
        addr = normalize_addr(r["from_addr"])
        if not addr:
            continue
        a = agg.setdefault(addr, {"sender": addr, "messages": 0, "doc_messages": 0,
                                  "first_ts": r["first_ts"], "last_ts": r["last_ts"],
                                  "sample_subject": None})
        a["messages"] += r["n"]
        a["last_ts"] = max(a["last_ts"], r["last_ts"])
        a["first_ts"] = min(a["first_ts"], r["first_ts"])

    doc_where = "AND m.deleted_at IS NULL" if live_only else ""
    for r in conn.execute(
            f"""SELECT m.from_addr, count(DISTINCT m.message_id) n,
                       max(m.subject) sample_subject,
                       group_concat(DISTINCT h.hint) hints
                  FROM mail_attachment_hints h
                  JOIN mail_messages m ON m.message_id = h.message_id
                 WHERE h.hint IN ({marks}) {doc_where}
                 GROUP BY m.from_addr""", hint_list):
        addr = normalize_addr(r["from_addr"])
        a = agg.get(addr)
        if not a:
            continue
        a["doc_messages"] += r["n"]
        a["sample_subject"] = a["sample_subject"] or r["sample_subject"]
        a["hints"] = sorted(set((a.get("hints") or []) + (
            (r["hints"] or "").split(",") if r["hints"] else [])))

    total = sum(a["messages"] for a in agg.values()) or 1
    out = sorted(agg.values(), key=lambda a: (-a["messages"], a["sender"]))
    running = 0
    for a in out:
        addr = a["sender"]
        a["sender_class"] = classes.get(addr, "unknown")
        a["verdict"] = verdict_for(addr, verdicts) or ""
        a["domain"] = domain_of(addr)
        a["hints"] = a.get("hints") or []
        a["excluded_by_class"] = a["sender_class"] in excluded
        running += a["messages"]
        a["cum_messages"] = running
        a["cum_coverage"] = running / total
    return out


def coverage_curve(census: Sequence[dict],
                   marks: Sequence[float] = (0.5, 0.8, 0.95)) -> dict[str, int]:
    """How many sender decisions buy how much of the corpus.

    This is the answer to "when do we stop": the corpus has a long tail, and
    the tail is not worth a decision each.
    """
    out: dict[str, int] = {}
    for m in marks:
        n = 0
        for i, row in enumerate(census, 1):
            if row["cum_coverage"] >= m:
                n = i
                break
        out[f"{int(m * 100)}%"] = n or len(census)
    return out


def suggest_verdict(row: dict) -> str:
    """A starting position for the operator, never a decision.

    Conservative in one direction only: it will suggest killing a marketing
    sender that has produced no document-shaped attachment in two years, and it
    will suggest keeping a human. Everything else is left for a human to look
    at, because 'review' costs a glance and a wrong 'never' is silent.
    """
    if row["sender_class"] == "marketing" and row["doc_messages"] == 0:
        return "never"
    if row["sender_class"] == "human":
        return "always"
    return "review"

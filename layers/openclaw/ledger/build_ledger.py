#!/usr/bin/env python3
"""Load a cascade run into the ledger database defined by schema.sql.

Input: the CSVs written by run_extract.py, plus the snapshot for entity
first/last-seen and contacts. Output: a self-contained ledger.sqlite.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "mail-enrichment"))

import entities as E                       # noqa: E402
import templates as T                      # noqa: E402
from cascade import ts_date                # noqa: E402

SNAP = os.environ.get("SNAP", "/tmp/mc-lifetime.sqlite")
# Sender classification is DERIVED, not archived. It used to be read from a
# records/ evidence directory — gitignored, and frozen at the 24-month corpus
# while the index grew to 18 years. Rebuild it with:
#   python3 ~/workspace/layers/openclaw/mail-enrichment/senders.py <snapshot>
PHASE0 = os.environ.get(
    "SENDERS_CSV",
    os.path.expanduser("~/.local/state/mail-enrichment/senders.csv"))
_ADDRISH = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)


def display_name(entity, counterparties: Counter) -> str:
    """An entity named by an email address is unusable in a ledger.

    `entities.py` falls back to the raw address when no display name survives
    its person-name test (a brokerage portal, NJ Transit MyTix). The
    counterparty the extractor recovered from the body text is a better label,
    so prefer it -- without touching the validated resolver.
    """
    name = entity.best_name()
    if _ADDRISH.match(name.strip()) and counterparties:
        return counterparties.most_common(1)[0][0]
    return name


def build(outdir: str, dbpath: str) -> dict:
    if not os.path.exists(SNAP):
        sys.exit(f"FATAL: snapshot {SNAP} is gone. Refusing to invent data.")
    snap = sqlite3.connect(f"file:{SNAP}?mode=ro", uri=True)
    classes = {r["sender"].lower(): r["class"] for r in csv.DictReader(open(PHASE0))}
    counts = snap.execute("select from_addr, count(*) from mail_messages "
                          "where deleted_at is null group by 1").fetchall()
    ents = E.build(counts, classes)
    a2k = {a: k for k, e in ents.items() for a in e.addresses}

    seen = defaultdict(lambda: [None, None])
    for fa, ts in snap.execute("select from_addr, internal_ts from mail_messages "
                               "where deleted_at is null"):
        a = E.address_of(fa)
        if not a:
            continue
        k = a2k[a]
        d = ts_date(ts)
        s = seen[k]
        s[0] = d if s[0] is None or d < s[0] else s[0]
        s[1] = d if s[1] is None or d > s[1] else s[1]

    rows = list(csv.DictReader(open(os.path.join(outdir, "transactions-raw.csv"))))
    primary = {r["message_id"] + "|" + r["kind"]
               for r in csv.DictReader(open(os.path.join(outdir, "transactions.csv")))}
    outcomes = list(csv.DictReader(open(os.path.join(outdir, "cascade-outcomes.csv"))))

    cp_by_entity = defaultdict(Counter)
    for r in rows:
        cp_by_entity[r["entity_key"]][r["counterparty"]] += 1

    if os.path.exists(dbpath):
        os.remove(dbpath)
    db = sqlite3.connect(dbpath)
    db.executescript(open(os.path.join(HERE, "schema.sql")).read())

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # One extraction_runs row per run that still owns at least one transaction.
    # run_extract.py is incremental, so a loaded ledger normally carries rows
    # from several runs: the id on each row is which extractor produced it, and
    # that is what makes "a better extractor supersedes rather than overwrites"
    # inspectable after the fact rather than a claim.
    state = {}
    state_path = os.path.join(outdir, "extract-state.json")
    if os.path.exists(state_path):
        state = {int(r["run_id"]): r for r in json.load(open(state_path)).get("runs", [])}
    msgs_in = Counter(o.get("run_id") or "1" for o in outcomes)
    rows_out = Counter(r.get("run_id") or "1" for r in rows)
    run_ids = sorted({int(r.get("run_id") or 1) for r in rows}
                     | {int(o.get("run_id") or 1) for o in outcomes})
    for rid in run_ids:
        meta = state.get(rid, {})
        db.execute(
            "INSERT INTO extraction_runs (run_id, started_at, finished_at, source, lane,"
            " extractor, model, messages_in, rows_out, notes) VALUES (?,?,?,?,?,?,NULL,?,?,?)",
            (rid, meta.get("started_at") or now, now,
             f"{os.path.basename(SNAP)} Gmail snippets",
             "deterministic", "ledger/cascade.py+templates.py v1",
             int(msgs_in.get(str(rid), 0)) or meta.get("messages_scanned", 0),
             int(rows_out.get(str(rid), 0)),
             f"mode={meta.get('mode', 'unknown')}; snippet-only, bodies not fetched."))

    ekeys: dict[str, int] = {}
    for key, e in ents.items():
        if key not in cp_by_entity and e.messages < 1:
            continue
        first, last = seen.get(key, (None, None))
        cur = db.execute(
            "INSERT INTO entities (entity_key,name,kind,domain,stream,first_seen,last_seen,"
            "message_count) VALUES (?,?,?,?,?,?,?,?)",
            (key, display_name(e, cp_by_entity.get(key, Counter())), e.kind,
             key.split(":", 1)[-1].split("#")[0] if e.kind == "merchant" else None,
             e.stream, first, last, e.messages))
        eid = cur.lastrowid
        ekeys[key] = eid
        for a in sorted(e.addresses):
            db.execute("INSERT OR IGNORE INTO entity_aliases (entity_id,alias,alias_type)"
                       " VALUES (?,?,'address')", (eid, a))
        for d, _ in e.display_names.most_common(6):
            db.execute("INSERT OR IGNORE INTO entity_aliases (entity_id,alias,alias_type)"
                       " VALUES (?,?,'display_name')", (eid, d))
        for cp, _ in cp_by_entity.get(key, Counter()).most_common(12):
            db.execute("INSERT OR IGNORE INTO entity_aliases (entity_id,alias,alias_type)"
                       " VALUES (?,?,'counterparty')", (eid, cp))

    # Contacts: a human display name on an organisation's address.
    for key, e in ents.items():
        if e.kind != "merchant" or key not in ekeys:
            continue
        for d, n in e.display_names.items():
            if E.looks_like_person_name(d):
                addr = sorted(e.addresses)[0] if len(e.addresses) == 1 else None
                db.execute("INSERT OR IGNORE INTO contacts (entity_id,person_name,address,"
                           "first_seen,last_seen,message_count) VALUES (?,?,?,?,?,?)",
                           (ekeys[key], d, addr, *seen.get(key, (None, None)), n))
    for key, e in ents.items():
        if e.kind == "person" and key in ekeys:
            db.execute("INSERT OR IGNORE INTO contacts (entity_id,person_name,address,"
                       "first_seen,last_seen,message_count) VALUES (?,?,?,?,?,?)",
                       (ekeys[key], e.best_name(), sorted(e.addresses)[0],
                        *seen.get(key, (None, None)), e.messages))

    # Templates.
    tpl = T.load()
    samples = defaultdict(list)
    for o in outcomes:
        if o["action"] == "extract" and o["sender"] in tpl and len(samples[o["sender"]]) < 5:
            samples[o["sender"]].append(o["message_id"])
    msg_seen = Counter(o["sender"] for o in outcomes)
    yielded = Counter()
    conf = defaultdict(list)
    for r in rows:
        s = r["rule"].split(":")[0]
        if s in tpl:
            yielded[s] += 1
            conf[s].append(float(r["confidence"]))
    for sender, t in tpl.items():
        eid = ekeys.get(a2k.get(sender, ""), None)
        db.execute(
            "INSERT INTO extraction_templates (sender,entity_id,rules_json,rule_count,"
            "default_action,learned_by,learned_at,run_id,confidence,messages_seen,"
            "rows_yielded,sample_message_ids,notes) VALUES (?,?,?,?,?,'human',?,?,?,?,?,?,?)",
            (sender, eid, json.dumps(t.raw_rules, ensure_ascii=False), len(t.raw_rules),
             t.default, now, run_ids[-1] if run_ids else 1,
             round(sum(conf[sender]) / len(conf[sender]), 3) if conf[sender] else None,
             msg_seen.get(sender, 0), yielded.get(sender, 0),
             json.dumps(samples.get(sender, [])), t.notes))

    for r in rows:
        eid = ekeys.get(r["entity_key"])
        if eid is None:
            continue
        db.execute(
            "INSERT OR IGNORE INTO transactions (entity_id,counterparty,date,kind,amount,"
            "currency,ref_number,service_dates,description,message_id,confidence,lane,rule,"
            "event_id,link_basis,is_primary,run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, r["counterparty"] or "?", r["date"], r["kind"],
             float(r["amount"]) if r["amount"] else None, r["currency"] or None,
             r["ref_number"] or None, r["service_dates"] or None, r["description"] or None,
             r["message_id"], float(r["confidence"]), r["lane"], r["rule"],
             int(r["event_id"]), r["link_basis"],
             1 if (r["message_id"] + "|" + r["kind"]) in primary else 0,
             int(r.get("run_id") or 1)))
    db.commit()
    stats = {t: db.execute(f"select count(*) from {t}").fetchone()[0]
             for t in ("entities", "entity_aliases", "contacts", "transactions",
                       "extraction_runs", "extraction_templates")}
    stats["ledger_view_rows"] = db.execute("select count(*) from ledger").fetchone()[0]
    db.close()
    return stats


if __name__ == "__main__":
    outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "out")
    dbpath = sys.argv[2] if len(sys.argv) > 2 else os.path.join(outdir, "ledger.sqlite")
    print(json.dumps(build(outdir, dbpath), indent=2))

"""CLI: build the bulk-harvest fetch list and the sender census.

Runs as the openclaw service account, because that is where the index lives.
Two outputs, both handed back to caio:

  candidates.tsv     the fetch list, best-first, for mailharvest.py
  sender-census.csv  one row per sender with the coverage curve -- the thing
                     the operator actually makes decisions on

`--report-only` does the whole thing without writing files and without an API
call, which is the gate: run it, read the count, then decide whether to fetch.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path

from mailctx import selection
from mailctx.store import connect
from mailctx.sync import DB_PATH


def _date(ts: int | None) -> str:
    if not ts:
        return ""
    return dt.datetime.fromtimestamp(ts, dt.UTC).date().isoformat()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mailctx.selectrun")
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--senders", type=Path,
                    help="phase-0 senders.csv (address-keyed classification)")
    ap.add_argument("--verdicts", type=Path,
                    help="operator sender-verdicts.csv; also loaded into sender_rules")
    ap.add_argument("--out", type=Path, help="write the fetch list here (TSV)")
    ap.add_argument("--census", type=Path, help="write the sender census here (CSV)")
    ap.add_argument("--limit", type=int, help="cap the fetch list (best-first)")
    ap.add_argument("--exclude-class", action="append", default=None,
                    help="repeatable; default: marketing")
    ap.add_argument("--include-marketing", action="store_true",
                    help="do not apply the layer-0 class filter at all")
    ap.add_argument("--no-subjects", action="store_true",
                    help="omit subjects from both outputs (they are PII)")
    ap.add_argument("--report-only", action="store_true",
                    help="print counts, write nothing")
    ap.add_argument("--json", type=Path, help="write the summary as JSON")
    a = ap.parse_args(argv)

    conn = connect(a.db)
    schema = Path(__file__).resolve().parent.parent / "schema.sql"
    conn.executescript(schema.read_text())      # all IF NOT EXISTS

    classes = selection.load_sender_classes(a.senders) if a.senders else {}
    verdicts = selection.load_rules(conn)
    if a.verdicts:
        from_csv = selection.load_verdicts(a.verdicts)
        if not a.report_only:
            selection.store_verdicts(conn, from_csv)
        verdicts.update(from_csv)

    hint_total = conn.execute(
        "SELECT count(*) FROM mail_attachment_hints").fetchone()[0]
    if hint_total == 0:
        print("mail_attachment_hints is EMPTY -- run `python3 -m mailctx.targetrun` "
              "first, or the fetch list will be empty for the wrong reason.",
              file=sys.stderr)

    excluded = () if a.include_marketing else tuple(
        a.exclude_class or selection.DEFAULT_EXCLUDE_CLASSES)

    cands = selection.select_candidates(
        conn, classes=classes, verdicts=verdicts,
        exclude_classes=excluded, limit=a.limit)
    census = selection.sender_census(
        conn, classes=classes, verdicts=verdicts, exclude_classes=excluded)
    curve = selection.coverage_curve(census)

    live = conn.execute(
        "SELECT count(*) FROM mail_messages WHERE deleted_at IS NULL").fetchone()[0]
    doc_hinted = conn.execute(
        f"""SELECT count(DISTINCT message_id) FROM mail_attachment_hints
             WHERE hint IN ({','.join('?' * len(selection.DOCUMENT_HINTS))})""",
        selection.DOCUMENT_HINTS).fetchone()[0]

    by_class: dict[str, int] = {}
    by_year: dict[str, int] = {}
    for c in cands:
        by_class[c["sender_class"]] = by_class.get(c["sender_class"], 0) + 1
        y = _date(c["internal_ts"])[:4]
        by_year[y] = by_year.get(y, 0) + 1

    summary = {
        "live_messages": live,
        "document_hinted_messages": doc_hinted,
        "excluded_classes": list(excluded),
        "verdict_rules": len(verdicts),
        "verdict_never": sum(1 for v in verdicts.values() if v == "never"),
        "candidates": len(cands),
        "candidates_by_sender_class": by_class,
        "candidates_by_year": dict(sorted(by_year.items())),
        "distinct_senders_in_candidates": len({c["sender"] for c in cands}),
        "senders_total": len(census),
        "senders_for_coverage": curve,
        "estimated_api_calls": len(cands) * 2,
    }
    print(json.dumps(summary, indent=1), file=sys.stderr)

    if a.json:
        a.json.write_text(json.dumps(summary, indent=1))
    if a.report_only:
        return 0

    if a.out:
        with open(a.out, "w", newline="") as f:
            w = csv.writer(f, delimiter="\t", lineterminator="\n")
            w.writerow(["category", "message_id", "date", "hints", "score",
                        "sender_class", "from", "subject"])
            for c in cands:
                w.writerow([
                    c["sender_class"], c["message_id"], _date(c["internal_ts"]),
                    "|".join(c["hints"]), c["score"], c["sender_class"], c["sender"],
                    "" if a.no_subjects else (c["subject"] or "").replace("\t", " ")[:120],
                ])
        print(f"wrote {a.out} ({len(cands)} candidates)", file=sys.stderr)

    if a.census:
        with open(a.census, "w", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["rank", "sender", "domain", "sender_class", "messages",
                        "doc_messages", "cum_messages", "cum_coverage", "first",
                        "last", "hints", "verdict", "suggested", "sample_subject"])
            for i, r in enumerate(census, 1):
                w.writerow([
                    i, r["sender"], r["domain"], r["sender_class"], r["messages"],
                    r["doc_messages"], r["cum_messages"], f"{r['cum_coverage']:.4f}",
                    _date(r["first_ts"]), _date(r["last_ts"]), "|".join(r["hints"]),
                    r["verdict"], selection.suggest_verdict(r),
                    "" if a.no_subjects else (r["sample_subject"] or "")[:100],
                ])
        print(f"wrote {a.census} ({len(census)} senders; "
              f"50%={curve['50%']} 80%={curve['80%']} 95%={curve['95%']} decisions)",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

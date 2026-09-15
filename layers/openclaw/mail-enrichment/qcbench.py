#!/usr/bin/env python3
"""qcbench — quality-control benchmark for model-lane classifications.

Flow: `sample` builds a stratified eval batch from the mail-context snapshot;
any model lane (local or cloud) answers the same prompt contract over the
batch, blind; `compare` scores one lane's answers against another's and emits
agreement, per-class confusion, and confidence calibration.

Design rules, matching docs/plans/openclaw-mail-artifacts-and-enrichment.md:
- Reviewer is BLIND: it re-classifies independently; it never sees the other
  lane's answer. Judge-mode adjudication happens only on disagreements, later.
- Items carry metadata only (sender, subject, snippet, labels) — the same
  input lane 1 itself sees. Marketing-class mail is excluded from cloud-bound
  samples by default (privacy gradient); --include-marketing overrides.
- Every batch and result file records run provenance; the egress list for a
  cloud run is exactly the batch file, reviewable before sending.

Stdlib only. No model is called from here: lanes are driven externally and
write answers as JSONL {item_id, class, confidence, reason}.
"""
import argparse, csv, json, random, sqlite3, sys, time
from collections import Counter, defaultdict
from pathlib import Path

CLASSES = ["human", "service-transactional", "service-notification",
           "marketing", "unknown"]

PROMPT = """You are classifying email senders for a personal mail archive.
Given one message's metadata, assign the SENDER one class:
- human: a real person writing to the recipient (including people using
  campaign/CRM mailing tools)
- service-transactional: receipts, orders, bookings, bills, account actions
- service-notification: automated status/social/forum notifications
- marketing: promotional/newsletter bulk mail
- unknown: genuinely undecidable from this metadata
Answer with JSON only: {"class": "...", "confidence": 0.0-1.0, "reason": "..."}
"""

def cmd_sample(a):
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True); con.row_factory = sqlite3.Row
    sender_class = {}
    if a.senders_csv:
        for r in csv.DictReader(open(a.senders_csv)):
            sender_class[r["sender"]] = r["class"]
    labels = defaultdict(list)
    for r in con.execute("SELECT message_id, label FROM mail_labels"):
        labels[r["message_id"]].append(r["label"])
    import re
    ADDR = re.compile(r'<([^>]+)>')
    def addr(raw):
        m = ADDR.search(raw or ""); x = (m.group(1) if m else raw or "").strip().lower()
        return x if "@" in x else None
    pools = defaultdict(list)
    for r in con.execute("""SELECT message_id, from_addr, subject, snippet, internal_ts
                            FROM mail_messages WHERE from_addr IS NOT NULL"""):
        labs = labels.get(r["message_id"], [])
        if "SENT" in labs: continue
        cls = sender_class.get(addr(r["from_addr"]), "unclassified")
        if cls == "marketing" and not a.include_marketing: continue
        pools[cls].append({
            "item_id": r["message_id"], "lane0_class": cls,
            "input": {"from": r["from_addr"], "subject": r["subject"],
                      "snippet": (r["snippet"] or "")[:300],
                      "labels": sorted(l for l in labs if not l.startswith("Label_"))}})
    rng = random.Random(a.seed)
    strata = sorted(pools)
    per = max(1, a.n // len(strata))
    batch = []
    for s in strata:
        pool = pools[s]; rng.shuffle(pool)
        batch.extend(pool[:per])
    rng.shuffle(batch)
    batch = batch[:a.n]
    meta = {"kind": "qcbench-batch", "created_at": int(time.time()), "seed": a.seed,
            "n": len(batch), "strata": {s: min(per, len(pools[s])) for s in strata},
            "marketing_included": a.include_marketing, "prompt": PROMPT}
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        f.write(json.dumps(meta) + "\n")
        for b in batch: f.write(json.dumps(b) + "\n")
    print(f"wrote {len(batch)} items to {out}", file=sys.stderr)
    print(json.dumps({"n": len(batch), "strata": meta["strata"]}, indent=1))

def _load_results(path):
    out = {}
    for line in open(path):
        line = line.strip()
        if not line: continue
        r = json.loads(line)
        if r.get("kind") == "qcbench-batch": continue
        cls = r.get("class")
        out[r["item_id"]] = {"class": cls if cls in CLASSES else "__invalid__",
                             "confidence": r.get("confidence"),
                             "raw_valid": cls in CLASSES}
    return out

def cmd_compare(a):
    A, B = _load_results(a.subject_results), _load_results(a.reviewer_results)
    ids = sorted(set(A) & set(B))
    if not ids: sys.exit("no overlapping item_ids")
    conf_matrix = Counter(); agree = 0; invalid_a = invalid_b = 0
    calib = defaultdict(lambda: [0, 0])  # conf bucket -> [n, agreed]
    disagreements = []
    for i in ids:
        ra, rb = A[i], B[i]
        if not ra["raw_valid"]: invalid_a += 1
        if not rb["raw_valid"]: invalid_b += 1
        conf_matrix[(ra["class"], rb["class"])] += 1
        ok = ra["class"] == rb["class"] and ra["raw_valid"]
        if ok: agree += 1
        else: disagreements.append({"item_id": i, "subject_class": ra["class"],
                                    "reviewer_class": rb["class"],
                                    "subject_confidence": ra["confidence"]})
        if isinstance(ra["confidence"], (int, float)):
            b = min(9, int(ra["confidence"] * 10)); calib[b][0] += 1; calib[b][1] += ok
    report = {
        "kind": "qcbench-report", "created_at": int(time.time()),
        "items": len(ids), "agreement": round(agree / len(ids), 3),
        "invalid_json_subject": invalid_a, "invalid_json_reviewer": invalid_b,
        "per_class": {c: {"n": sum(v for (x, _), v in conf_matrix.items() if x == c),
                          "agreed": conf_matrix.get((c, c), 0)} for c in CLASSES},
        "confusion_top": [{"subject": x, "reviewer": y, "n": n} for (x, y), n
                          in conf_matrix.most_common() if x != y][:10],
        "calibration": {f"{b/10:.1f}-{(b+1)/10:.1f}":
                        {"n": v[0], "agreement": round(v[1]/v[0], 2)}
                        for b, v in sorted(calib.items()) if v[0]},
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    json.dump(report, open(a.out, "w"), indent=1)
    dq = Path(a.out).with_suffix(".disagreements.jsonl")
    with open(dq, "w") as f:
        for d in disagreements: f.write(json.dumps(d) + "\n")
    print(json.dumps(report, indent=1))
    print(f"\ndisagreement queue: {dq} ({len(disagreements)} items)", file=sys.stderr)

def main():
    ap = argparse.ArgumentParser(prog="qcbench")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample", help="build a stratified eval batch (JSONL)")
    s.add_argument("--db", required=True); s.add_argument("--out", required=True)
    s.add_argument("--senders-csv", help="lane-0 senders.csv for stratification")
    s.add_argument("--n", type=int, default=300); s.add_argument("--seed", type=int, default=20260824)
    s.add_argument("--include-marketing", action="store_true")
    s.set_defaults(fn=cmd_sample)
    c = sub.add_parser("compare", help="score subject lane against blind reviewer lane")
    c.add_argument("--subject-results", required=True, help="e.g. local-qwen answers JSONL")
    c.add_argument("--reviewer-results", required=True, help="e.g. cloud `work` answers JSONL")
    c.add_argument("--out", required=True)
    c.set_defaults(fn=cmd_compare)
    a = ap.parse_args(); a.fn(a)

if __name__ == "__main__":
    main()

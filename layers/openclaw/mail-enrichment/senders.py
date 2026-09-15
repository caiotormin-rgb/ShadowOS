"""Sender classification — the cascade's free layer-0 filter.

Was previously computed once into a records/ evidence directory and read from
there by production code. That was wrong three ways: records are immutable
evidence rather than live inputs, the file is gitignored so a fresh clone
could not run the ledger, and it was frozen at the 24-month corpus while the
index grew to 18 years.

It is now derived on demand from whatever snapshot exists, written to state,
and versioned by the corpus it was built from.
"""
from __future__ import annotations

import csv, os, re, sqlite3, time
from collections import defaultdict
from pathlib import Path

OUT = Path(os.environ.get(
    "SENDERS_CSV", Path.home() / ".local/state/mail-enrichment/senders.csv"))

ADDR = re.compile(r"<([^>]+)>")
TRANSACT = re.compile(
    r"\b(order|receipt|invoice|statement|confirmation|confirm(ed)?|booking|"
    r"reservation|itinerary|ticket|shipped|shipping|deliver(y|ed)|payment|paid|"
    r"bill|due|renewal|policy|claim|appointment|verify|verification|"
    r"security alert|sign-?in|password|OTP|code|fatura|pedido|recibo|pagamento|"
    r"comprovante|agendamento)\b", re.I)
MARKETING_LOCAL = re.compile(
    r"^(news(letter)?|marketing|promo(tions)?|offers?|deals?|store-news|email|"
    r"hello|info|reply|noreply|no-reply|donotreply|concierge|team)$", re.I)
BULK_SUBDOM = re.compile(
    r"^(e|em|e2|em2|mail|email|news|newsletter|marketing|info|reply|hello|"
    r"deals|offers|mta|bounce|mailer|links|click|shared\d*)\.")

FIELDS = ["sender", "display_name", "class", "msg_count", "read_rate",
          "promo_ratio", "transact_ratio", "important", "inbox",
          "first", "last", "evidence", "corpus_messages", "generated_at"]


def address_of(raw):
    if not raw: return None
    m = ADDR.search(raw)
    a = (m.group(1) if m else raw).strip().strip('"').lower()
    return a if "@" in a else None


def classify(s, replied_to) -> tuple[str, str]:
    local, _, dom = s["addr"].partition("@")
    n = s["n"]
    promo, upd = s["promo"] / n, s["updates"] / n
    pers, trans = s["personal"] / n, s["transact"] / n
    bulk = bool(MARKETING_LOCAL.match(local)) or bool(BULK_SUBDOM.match(dom))
    if s["addr"] in replied_to and promo < 0.5:
        return "human", "operator has written to this address"
    if pers > 0.5 and n <= 20 and not bulk:
        return "human", "personal category, low volume, non-bulk address"
    if promo > 0.6:            return "marketing", f"{promo:.0%} promotions"
    if trans > 0.4 and promo < 0.3:
        return "service-transactional", f"{trans:.0%} transactional subjects"
    if upd > 0.6 and bulk:     return "service-notification", "updates category, bulk shape"
    if s["social"] / n > 0.6:  return "service-notification", "social category"
    if bulk:                   return "marketing", "bulk address shape"
    return "unknown", "no rule fired"


def build(snapshot: str, out: Path = OUT) -> dict:
    c = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    labels = defaultdict(set)
    for r in c.execute("SELECT message_id, label FROM mail_labels"):
        labels[r["message_id"]].add(r["label"])
    replied_to, senders, corpus = set(), {}, 0
    for r in c.execute("""SELECT message_id, from_addr, to_addrs, subject, internal_ts
                            FROM mail_messages"""):
        corpus += 1
        labs = labels.get(r["message_id"], set())
        if "SENT" in labs:
            for part in (r["to_addrs"] or "").split(","):
                a = address_of(part)
                if a: replied_to.add(a)
            continue
        fa = address_of(r["from_addr"])
        if not fa: continue
        s = senders.setdefault(fa, {
            "addr": fa, "name": ADDR.sub("", r["from_addr"] or "").strip().strip('"'),
            "n": 0, "read": 0, "promo": 0, "updates": 0, "personal": 0,
            "social": 0, "important": 0, "inbox": 0, "transact": 0,
            "first": r["internal_ts"], "last": 0})
        s["n"] += 1
        s["first"] = min(s["first"], r["internal_ts"]); s["last"] = max(s["last"], r["internal_ts"])
        if "UNREAD" not in labs: s["read"] += 1
        for lab, k in (("CATEGORY_PROMOTIONS","promo"), ("CATEGORY_UPDATES","updates"),
                       ("CATEGORY_PERSONAL","personal"), ("CATEGORY_SOCIAL","social"),
                       ("IMPORTANT","important"), ("INBOX","inbox")):
            if lab in labs: s[k] += 1
        if r["subject"] and TRANSACT.search(r["subject"]): s["transact"] += 1

    now = int(time.time())
    rows = []
    for s in senders.values():
        cls, why = classify(s, replied_to)
        rows.append({
            "sender": s["addr"], "display_name": s["name"], "class": cls,
            "msg_count": s["n"], "read_rate": round(s["read"]/s["n"], 3),
            "promo_ratio": round(s["promo"]/s["n"], 2),
            "transact_ratio": round(s["transact"]/s["n"], 2),
            "important": s["important"], "inbox": s["inbox"],
            "first": time.strftime("%Y-%m-%d", time.gmtime(s["first"])),
            "last": time.strftime("%Y-%m-%d", time.gmtime(s["last"])),
            "evidence": why, "corpus_messages": corpus, "generated_at": now})
    rows.sort(key=lambda r: -r["msg_count"])
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); w.writerows(rows)
    out.chmod(0o600)   # sender addresses are personal data
    from collections import Counter
    return {"senders": len(rows), "corpus_messages": corpus, "path": str(out),
            "by_class": dict(Counter(r["class"] for r in rows))}


def load(path: Path = OUT) -> dict[str, str]:
    """sender address -> class. The one function production code should use."""
    if not path.exists():
        raise SystemExit(
            f"sender classification missing: {path}\n"
            f"  build it:  python3 -m mail_enrichment_senders <snapshot.sqlite>\n"
            f"  or:        python3 {Path(__file__).name} <snapshot.sqlite>")
    with open(path) as f:
        return {r["sender"]: r["class"] for r in csv.DictReader(f)}


if __name__ == "__main__":
    import json, sys
    if len(sys.argv) < 2:
        raise SystemExit("usage: senders.py <snapshot.sqlite> [out.csv]")
    dest = Path(sys.argv[2]) if len(sys.argv) > 2 else OUT
    print(json.dumps(build(sys.argv[1], dest), indent=1))

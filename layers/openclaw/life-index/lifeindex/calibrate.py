"""The calibration report: what the harvest actually brought back.

This exists so a wide, deliberately-unfiltered first pass can be *read* and
turned into thresholds. It is not a status page -- `cli status` is that. It is
an argument, organised so that the cheapest decisions come first.

Organised BY SENDER, because that is what the corpus measurement says is the
only durable key: 41 senders cover 50% of the non-marketing mail, 154 cover
80%, and only 2% of messages come from a sender seen once, while subject-
derived keys are 82% singletons. So the operator makes ~154 sender decisions,
not 20,250 message decisions, and each one is cached forever in `sender_rules`
and terminates at layer 0 on every future run.

Everything downstream of the sender list -- types, years, the big documents,
the untyped pile, the extraction failures, the random sample -- exists to
justify those decisions, not to replace them.

Output contains PII: subjects, senders, and document excerpts. It belongs
inside the store, not in the repo.
"""
from __future__ import annotations

import csv
import json
import random
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

SAMPLE_CHARS = 200
DEFAULT_SAMPLE = 30
DEFAULT_TOP_SENDERS = 60


# -- inputs beyond the catalog ------------------------------------------

def read_manifest(path: Path) -> list[dict]:
    """The fetch-side record: what was downloaded, skipped, and failed.

    The catalog cannot answer "how much was chrome" because chrome is never
    cataloged -- that is the whole point of skipping it. Only the manifest
    knows what the harvest declined to keep, so noise is measurable rather
    than merely asserted.
    """
    rows = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        return []
    return rows


def read_census(path: Path) -> dict[str, dict]:
    """The corpus-side sender census from `mailctx.selectrun`, keyed by address.

    Without it the report can only rank senders by what was harvested, which
    understates a sender that sends 800 messages and two documents -- exactly
    the shape most worth a 'never'.
    """
    out: dict[str, dict] = {}
    try:
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                addr = (row.get("sender") or "").strip().lower()
                if not addr:
                    continue
                out[addr] = {
                    "messages": int(row.get("messages") or 0),
                    "doc_messages": int(row.get("doc_messages") or 0),
                    "sender_class": row.get("sender_class") or "",
                    "cum_coverage": float(row.get("cum_coverage") or 0),
                    "suggested": row.get("suggested") or "",
                }
    except (OSError, ValueError):
        return {}
    return out


# -- the sender view ----------------------------------------------------

@dataclass
class SenderRow:
    addr: str
    name: str = ""
    artifacts: int = 0
    bytes: int = 0
    tiers: dict[int, int] = field(default_factory=dict)
    doc_types: dict[str, int] = field(default_factory=dict)
    review: int = 0
    messages_harvested: int = 0
    chrome_skipped: int = 0
    sample_title: str = ""
    corpus_messages: int = 0
    corpus_doc_messages: int = 0
    sender_class: str = ""

    @property
    def real_documents(self) -> int:
        """Artifacts that are not email bodies. A sender whose only output is
        correspondence is a different decision from one that sends contracts."""
        return self.artifacts - self.doc_types.get("correspondence", 0)

    @property
    def yield_rate(self) -> float:
        base = self.corpus_messages or self.messages_harvested
        return (self.real_documents / base) if base else 0.0


def sender_rows(cat, *, census: dict[str, dict] | None = None,
                manifest: Sequence[dict] = ()) -> list[SenderRow]:
    census = census or {}
    rows: dict[str, SenderRow] = {}

    for r in cat.conn.execute(
            """SELECT coalesce(c.primary_addr, '(unknown)') addr,
                      coalesce(c.name, '(unknown)') name,
                      a.sha256, a.bytes, a.tier, a.doc_type, a.needs_review,
                      a.title, a.original_name, a.source_ref
                 FROM artifacts a
                 LEFT JOIN correspondents c ON c.id = a.correspondent"""):
        row = rows.setdefault(r["addr"], SenderRow(r["addr"], r["name"]))
        row.artifacts += 1
        row.bytes += r["bytes"] or 0
        row.tiers[r["tier"]] = row.tiers.get(r["tier"], 0) + 1
        dt_ = r["doc_type"] or "(untyped)"
        row.doc_types[dt_] = row.doc_types.get(dt_, 0) + 1
        row.review += 1 if r["needs_review"] else 0
        if not row.sample_title and dt_ != "correspondence":
            row.sample_title = (r["title"] or r["original_name"] or "")[:80]

    # Messages and chrome come from the manifest: a message that yielded
    # nothing but logos has no artifact and would otherwise be invisible --
    # and "sends 300 messages, produced 0 documents" is the single most
    # actionable line in the report.
    for m in manifest:
        addr = _addr_of(m.get("sender"))
        if not addr:
            continue
        row = rows.setdefault(addr, SenderRow(addr, addr))
        row.messages_harvested += 1
        row.chrome_skipped += sum(
            1 for s in (m.get("skipped") or []) if s.get("reason") == "chrome")

    for addr, row in rows.items():
        c = census.get(addr)
        if c:
            row.corpus_messages = c["messages"]
            row.corpus_doc_messages = c["doc_messages"]
            row.sender_class = c["sender_class"]
        if not row.sample_title:
            row.sample_title = "(no document; bodies only)"
    return sorted(rows.values(),
                  key=lambda r: (-r.real_documents, -r.artifacts, r.addr))


def _addr_of(raw: str | None) -> str:
    if not raw:
        return ""
    import re
    m = re.search(r"<([^>]+)>", raw)
    return (m.group(1) if m else raw).strip().strip('"').lower()


def suggest(row: SenderRow) -> tuple[str, str]:
    """(verdict, why). A starting position, never a decision.

    Asymmetric on purpose. 'never' is only suggested where the evidence is
    that the sender produced no document at all across everything harvested,
    because a wrong 'never' is silent forever and a wrong 'review' costs a
    glance.
    """
    if row.real_documents == 0 and row.messages_harvested >= 3:
        return "never", f"{row.messages_harvested} messages, 0 documents"
    if row.real_documents == 0:
        return "review", "no documents yet, too few messages to judge"
    if row.tiers.get(1, 0):
        return "always", f"{row.tiers[1]} tier-1 document(s)"
    if row.real_documents >= 3 and row.doc_types.get("(untyped)", 0) == row.real_documents:
        return "review", "documents, but none classified — rules gap"
    verdict = "always" if row.real_documents >= 2 else "review"
    return verdict, f"{row.real_documents} document(s)"


def coverage_curve(rows: Sequence[SenderRow], key=lambda r: r.artifacts,
                   marks: Sequence[float] = (0.5, 0.8, 0.95)) -> dict[str, int]:
    total = sum(key(r) for r in rows) or 1
    ranked = sorted(rows, key=lambda r: -key(r))
    out: dict[str, int] = {}
    for m in marks:
        run = 0
        out[f"{int(m * 100)}%"] = len(ranked)
        for i, r in enumerate(ranked, 1):
            run += key(r)
            if run / total >= m:
                out[f"{int(m * 100)}%"] = i
                break
    return out


# -- rendering ----------------------------------------------------------

def _h(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return str(n)


def _table(headers: Sequence[str], rows: Iterable[Sequence]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |")
    return "\n".join(out)


def _mix(d: dict) -> str:
    return ", ".join(f"{k}×{v}" for k, v in
                     sorted(d.items(), key=lambda kv: -kv[1])[:4]) or "—"


def build_report(cat, *, manifest_path: Path | None = None,
                 census_path: Path | None = None, sample_n: int = DEFAULT_SAMPLE,
                 top_senders: int = DEFAULT_TOP_SENDERS, seed: int = 0,
                 store_mode: str = "unknown") -> tuple[str, list[SenderRow], dict]:
    manifest = read_manifest(manifest_path) if manifest_path else []
    census = read_census(census_path) if census_path else {}
    senders = sender_rows(cat, census=census, manifest=manifest)
    q = lambda sql, *p: cat.conn.execute(sql, p).fetchall()
    one = lambda sql, *p: cat.conn.execute(sql, p).fetchone()[0]

    total = one("SELECT count(*) FROM artifacts")
    total_bytes = one("SELECT coalesce(sum(bytes),0) FROM artifacts")
    gmail = one("SELECT count(*) FROM artifacts WHERE source='gmail'")
    bodies = one("SELECT count(*) FROM artifacts WHERE doc_type='correspondence'")
    docs = total - bodies
    review = one("SELECT count(*) FROM artifacts WHERE needs_review=1")
    untyped = one("SELECT count(*) FROM artifacts WHERE doc_type IS NULL")

    chrome = sum(1 for m in manifest for s in (m.get("skipped") or [])
                 if s.get("reason") == "chrome")
    chrome_bytes = sum(s.get("size", 0) for m in manifest
                       for s in (m.get("skipped") or []) if s.get("reason") == "chrome")
    oversize = sum(1 for m in manifest for s in (m.get("skipped") or [])
                   if s.get("reason") == "oversize")
    fetch_failed = [m for m in manifest if m.get("status") != "ok"]
    msgs = len(manifest)
    empty_msgs = sum(1 for m in manifest
                     if m.get("status") == "ok" and not (m.get("attachments") or []))

    curve_art = coverage_curve(senders, key=lambda r: r.artifacts)
    curve_doc = coverage_curve(senders, key=lambda r: r.real_documents)

    L: list[str] = []
    A = L.append
    A(f"# Gmail bulk harvest — calibration report\n")
    A(f"Generated {time.strftime('%Y-%m-%d %H:%M:%S')}. "
      f"Store mode: **{store_mode}**. Seed: {seed}.\n")
    A("> Contains subjects, sender addresses and document excerpts. This file is\n"
      "> PII. Keep it inside the store; do not commit it.\n")

    A("## The one-line verdict\n")
    if msgs:
        A(f"{msgs:,} messages fetched → **{docs:,} documents** kept "
          f"({_h(total_bytes)}), {bodies:,} email bodies, "
          f"{chrome:,} chrome attachments skipped ({_h(chrome_bytes)} never downloaded). "
          f"{empty_msgs:,} messages ({empty_msgs / max(1, msgs):.0%}) yielded no document at all.\n")
    else:
        A(f"{total:,} artifacts in the catalog ({_h(total_bytes)}); "
          f"{docs:,} documents, {bodies:,} email bodies. "
          f"No fetch manifest supplied, so fetch-side noise is not measured.\n")

    # ---- 1. the decision list -----------------------------------------
    A("## 1. Senders, ranked — this is the list to act on\n")
    A("One decision per sender, cached forever. `never` terminates at layer 0 on\n"
      "every future run: no API call, no bytes, no model call. `always` fetches\n"
      "regardless of the phase-0 class. Edit the `verdict` column of\n"
      "`sender-verdicts.csv` next to this report; `suggested` is a starting\n"
      "position, not a decision.\n")
    head = ["#", "sender", "class", "msgs", "docs", "bytes", "types", "T1/T2/T3",
            "sample", "suggested", "why"]
    body = []
    for i, r in enumerate(senders[:top_senders], 1):
        v, why = suggest(r)
        body.append([
            i, r.addr[:42], r.sender_class or "—",
            r.corpus_messages or r.messages_harvested or "—",
            r.real_documents, _h(r.bytes), _mix(r.doc_types),
            f"{r.tiers.get(1,0)}/{r.tiers.get(2,0)}/{r.tiers.get(3,0)}",
            (r.sample_title or "")[:44], v, why])
    A(_table(head, body))
    if len(senders) > top_senders:
        A(f"\n…{len(senders) - top_senders} more senders in `sender-verdicts.csv`.\n")

    # ---- 2. cost curve -------------------------------------------------
    A("\n## 2. The cost curve — when to stop deciding\n")
    A(_table(["coverage of", "50%", "80%", "95%", "total senders"], [
        ["harvested artifacts", curve_art["50%"], curve_art["80%"],
         curve_art["95%"], len(senders)],
        ["real documents", curve_doc["50%"], curve_doc["80%"],
         curve_doc["95%"], len(senders)],
    ]))
    if census:
        cens_sorted = sorted(census.items(), key=lambda kv: -kv[1]["messages"])
        tot = sum(v["messages"] for v in census.values()) or 1
        marks, run, res = (0.5, 0.8, 0.95), 0, {}
        for i, (_, v) in enumerate(cens_sorted, 1):
            run += v["messages"]
            for m in marks:
                if f"{int(m*100)}%" not in res and run / tot >= m:
                    res[f"{int(m*100)}%"] = i
        A("\nAgainst the **whole indexed corpus** (not just what was harvested):\n")
        A(_table(["coverage of", "50%", "80%", "95%", "total senders"],
                 [["all indexed messages", res.get("50%", "?"), res.get("80%", "?"),
                   res.get("95%", "?"), len(census)]]))
    A("\nThe tail is long and flat. Decide the head, leave the tail on `review`,\n"
      "and re-read this table after the next run rather than grinding to zero.\n")

    # ---- 3. what came back --------------------------------------------
    A("\n## 3. What made it in — type × tier\n")
    A(_table(["doc_type", "tier", "n", "total bytes", "median bytes", "needs_review"],
             [[r["doc_type"] or "(untyped)", r["tier"], r["n"], _h(r["b"] or 0),
               _h(r["med"] or 0), r["rev"]]
              for r in q("""SELECT doc_type, tier, count(*) n, sum(bytes) b,
                                   avg(bytes) med, sum(needs_review) rev
                              FROM artifacts GROUP BY doc_type, tier
                             ORDER BY tier, n DESC""")]))

    A("\n### By year\n")
    A(_table(["year", "artifacts", "documents", "bytes"],
             [[r["y"] or "(no date)", r["n"], r["d"], _h(r["b"] or 0)]
              for r in q("""SELECT substr(coalesce(doc_date,''),1,4) y, count(*) n,
                                   sum(doc_type IS NOT 'correspondence') d, sum(bytes) b
                              FROM artifacts GROUP BY y ORDER BY y""")]))

    # ---- 4. biggest ----------------------------------------------------
    A("\n## 4. The biggest documents\n")
    A("Size is the cheapest proxy for substance in an unclassified pile.\n")
    A(_table(["bytes", "type", "T", "title", "sender", "date"],
             [[_h(r["bytes"]), r["doc_type"] or "(untyped)", r["tier"],
               (r["title"] or r["original_name"] or "")[:58],
               (r["addr"] or "")[:32], r["doc_date"] or "—"]
              for r in q("""SELECT a.*, c.primary_addr addr FROM artifacts a
                            LEFT JOIN correspondents c ON c.id=a.correspondent
                            WHERE a.doc_type IS NOT 'correspondence'
                            ORDER BY a.bytes DESC LIMIT 25""")]))

    # ---- 5. the untyped pile ------------------------------------------
    A(f"\n## 5. The '(untyped)' pile — {untyped:,} artifacts\n")
    A("Every row here is a classification rule that does not exist yet. This is\n"
      "the section that tells us which `classify.RULES` patterns to add.\n")
    A(_table(["bytes", "chars", "title", "filename", "sender"],
             [[_h(r["bytes"]), r["chars"] or 0,
               (r["title"] or "")[:50], (r["original_name"] or "")[:36],
               (r["addr"] or "")[:30]]
              for r in q("""SELECT a.bytes, a.title, a.original_name, t.chars,
                                   c.primary_addr addr
                              FROM artifacts a
                              LEFT JOIN artifact_text t ON t.sha256=a.sha256
                              LEFT JOIN correspondents c ON c.id=a.correspondent
                             WHERE a.doc_type IS NULL
                             ORDER BY a.bytes DESC LIMIT 40""")]))

    # ---- 6. failures ---------------------------------------------------
    A(f"\n## 6. Extraction failures — {review:,} flagged\n")
    A("Nothing is silently unsearchable: these were kept and flagged. A cluster\n"
      "of PDFs here means scans, i.e. the tesseract/Docling lane is now needed.\n")
    A(_table(["ext", "n", "reason", "example"],
             [[r["ext"] or "—", r["n"], (r["review_reason"] or "")[:40],
               (r["original_name"] or "")[:44]]
              for r in q("""SELECT ext, count(*) n, review_reason,
                                   max(original_name) original_name
                              FROM artifacts WHERE needs_review=1
                             GROUP BY ext, review_reason ORDER BY n DESC LIMIT 20""")]))

    # ---- 7. fetch-side noise ------------------------------------------
    A("\n## 7. Fetch-side noise and failures\n")
    if manifest:
        errs: dict[str, int] = {}
        for m in fetch_failed:
            errs[m.get("error_class") or "?"] = errs.get(m.get("error_class") or "?", 0) + 1
        A(_table(["measure", "count", "note"], [
            ["messages fetched", msgs, ""],
            ["messages with no document", empty_msgs,
             f"{empty_msgs / max(1, msgs):.0%} — the pointer/correspondence class"],
            ["chrome attachments skipped", chrome, f"{_h(chrome_bytes)} never downloaded"],
            ["oversize attachments skipped", oversize, "above --max-attachment-bytes"],
            ["messages failed", len(fetch_failed),
             ", ".join(f"{k}×{v}" for k, v in errs.items()) or "—"],
        ]))
    else:
        A("_No manifest supplied (`--manifest`), so fetch-side noise is unmeasured._\n")

    # ---- 8. the sample -------------------------------------------------
    rng = random.Random(seed)
    pool = [dict(r) for r in q(
        """SELECT a.sha256, a.title, a.original_name, a.doc_type, a.tier, a.bytes,
                  c.primary_addr addr, t.text
             FROM artifacts a
             LEFT JOIN correspondents c ON c.id = a.correspondent
             LEFT JOIN artifact_text t ON t.sha256 = a.sha256""")]
    rng.shuffle(pool)
    picked = pool[:sample_n]
    A(f"\n## 8. Random sample of {len(picked)} — eyeball precision here\n")
    A(f"Seeded ({seed}), so this is reproducible and re-runnable after a rule change.\n")
    for i, r in enumerate(picked, 1):
        head_ = (r["title"] or r["original_name"] or "(untitled)")[:88]
        excerpt = " ".join((r["text"] or "").split())[:SAMPLE_CHARS]
        A(f"**{i}. {head_}**  \n"
          f"`{r['doc_type'] or '(untyped)'}` · T{r['tier']} · {_h(r['bytes'])} · "
          f"{(r['addr'] or '—')[:40]} · `{r['sha256'][:12]}`\n")
        A(textwrap.indent(textwrap.fill(excerpt or "(no text extracted)", 92), "> ") + "\n")

    # ---- 9. levers -----------------------------------------------------
    A("\n## 9. Calibration levers, with what each would cost\n")
    A("Each row is a threshold Caio can set, and the number of *currently kept*\n"
      "artifacts it would drop. Nothing here is applied.\n")
    levers = []
    for label, sql in [
        ("drop email bodies (keep attachments only)",
         "SELECT count(*) FROM artifacts WHERE doc_type='correspondence'"),
        ("drop artifacts under 20 KB", "SELECT count(*) FROM artifacts WHERE bytes < 20000"),
        ("drop artifacts under 50 KB", "SELECT count(*) FROM artifacts WHERE bytes < 50000"),
        ("drop tier 3 entirely", "SELECT count(*) FROM artifacts WHERE tier=3"),
        ("drop untyped artifacts", "SELECT count(*) FROM artifacts WHERE doc_type IS NULL"),
        ("drop artifacts with no extracted text",
         "SELECT count(*) FROM artifacts WHERE needs_review=1"),
        ("keep only tier 1 + tier 2", "SELECT count(*) FROM artifacts WHERE tier=3"),
    ]:
        n = one(sql)
        levers.append([label, n, f"{n / max(1, total):.0%} of the catalog"])
    zero_doc_senders = [r for r in senders if r.real_documents == 0 and r.messages_harvested >= 3]
    levers.append([f"apply the {len(zero_doc_senders)} suggested sender 'never' verdicts",
                   sum(r.messages_harvested for r in zero_doc_senders),
                   "messages never fetched next run"])
    A(_table(["lever", "artifacts affected", "share"], levers))

    stats = {
        "artifacts": total, "documents": docs, "bodies": bodies,
        "bytes": total_bytes, "gmail_artifacts": gmail, "needs_review": review,
        "untyped": untyped, "senders": len(senders),
        "messages_fetched": msgs, "messages_without_document": empty_msgs,
        "chrome_skipped": chrome, "chrome_bytes_avoided": chrome_bytes,
        "oversize_skipped": oversize, "fetch_failures": len(fetch_failed),
        "sender_decisions_for_50pct_docs": curve_doc["50%"],
        "sender_decisions_for_80pct_docs": curve_doc["80%"],
        "suggested_never": len(zero_doc_senders),
    }
    return "\n".join(L) + "\n", senders, stats


def write_verdicts_csv(path: Path, senders: Sequence[SenderRow]) -> int:
    """The editable artifact. Caio changes the `verdict` column and hands it
    back to `mailctx.selectrun --verdicts`, which persists it into
    `sender_rules` and terminates those senders at layer 0 forever after."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["sender", "verdict", "reason", "suggested", "sender_class",
                    "corpus_messages", "harvested_messages", "documents",
                    "bytes", "doc_types", "sample_title"])
        for r in senders:
            v, why = suggest(r)
            w.writerow([r.addr, "", "", v, r.sender_class, r.corpus_messages,
                        r.messages_harvested, r.real_documents, r.bytes,
                        _mix(r.doc_types), (r.sample_title or "")[:80]])
    return len(senders)

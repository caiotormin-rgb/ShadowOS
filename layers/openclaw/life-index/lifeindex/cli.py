"""life-index CLI."""
from __future__ import annotations

import argparse, json, sys, textwrap
from pathlib import Path

from . import paths
from .consume import consume_dir, consume_file
from .harvest import import_harvest
from .store import Catalog, apply_schema, connect


def _catalog(write: bool = True) -> Catalog:
    paths.require_store()
    paths.BLOBS.mkdir(parents=True, exist_ok=True)
    paths.CONSUME.mkdir(parents=True, exist_ok=True)
    conn = connect(paths.DB, read_only=not write)
    if write:
        apply_schema(conn)
    return Catalog(conn)


def cmd_consume(a) -> int:
    cat = _catalog()
    run = cat.start_run(lane=0, model="deterministic", version="v1")
    src = Path(a.path) if a.path else paths.CONSUME
    if src.is_file():
        results = [consume_file(src, cat, source=a.source, run_id=run)]
    elif a.harvest:
        results = import_harvest(src, cat, run_id=run)
    else:
        results = consume_dir(src, cat, source=a.source, run_id=run,
                              move_done=not a.keep)
    cat.finish_run(run, sum(1 for r in results if r.status == "ok"))
    for r in results:
        line = f"{r.status:15} {r.path.name[:52]:52}"
        if r.status == "ok":
            line += f" {r.doc_type or '-':12} T{r.tier} {r.chars:>7}ch {r.n_fields:>2}f"
        if r.note:
            line += f"  ({r.note})"
        print(line)
    ok = sum(1 for r in results if r.status == "ok")
    print(f"\n{ok} cataloged, {len(results) - ok} skipped/duplicate/failed", file=sys.stderr)
    return 0


def cmd_search(a) -> int:
    cat = _catalog(write=False)
    for r in cat.search(" ".join(a.query), limit=a.limit):
        print(f"{r['sha256'][:12]}  T{r['tier']} {r['doc_type'] or '-':12} "
              f"{(r['title'] or r['original_name'] or '')[:56]}")
        if r.get("excerpt"):
            print(textwrap.indent(textwrap.fill(r["excerpt"], 88), "              "))
    return 0


def cmd_get(a) -> int:
    cat = _catalog(write=False)
    row = cat.get(a.sha256)
    if not row:
        print("not found", file=sys.stderr)
        return 1
    row["path"] = str(cat.blob_path(row["sha256"]))
    print(json.dumps(row, indent=1, ensure_ascii=False))
    return 0


def cmd_status(a) -> int:
    mode = paths.mode()
    if mode == "unavailable":
        print(f"store   : NOT AVAILABLE ({paths.STORE})")
        print("          bin/li-init && bin/li-mount   (encrypted)")
        print("          bin/li-plaintext              (unencrypted, explicit)")
        return 1
    if mode == "plaintext":
        print("store   : \033[33mPLAINTEXT — NOT ENCRYPTED\033[0m "
              f"({paths.PLAIN})")
        print("          documents are readable by anyone with disk access")
        print("          to encrypt later: bin/li-encrypt")
    cat = _catalog(write=False)
    q = lambda s: cat.conn.execute(s).fetchone()[0]
    if mode == "encrypted":
        print(f"store    : encrypted, mounted at {paths.PLAIN}")
    print(f"artifacts: {q('SELECT count(*) FROM artifacts'):,}")
    print(f"  tier 1 : {q('SELECT count(*) FROM artifacts WHERE tier=1')}")
    print(f"  tier 2 : {q('SELECT count(*) FROM artifacts WHERE tier=2')}")
    print(f"  tier 3 : {q('SELECT count(*) FROM artifacts WHERE tier=3')}")
    print(f"  review : {q('SELECT count(*) FROM artifacts WHERE needs_review=1')}")
    print(f"text     : {q('SELECT count(*) FROM artifact_text'):,} extracted")
    print(f"fields   : {q('SELECT count(*) FROM artifact_fields'):,}")
    for r in cat.conn.execute(
            "SELECT doc_type, count(*) n FROM artifacts GROUP BY doc_type ORDER BY n DESC"):
        print(f"  {r['doc_type'] or '(untyped)':14} {r['n']}")
    return 0


def cmd_calibrate(a) -> int:
    """The report Caio reads to decide what is signal. Written into the store,
    because it quotes document text and sender addresses."""
    from . import calibrate
    cat = _catalog(write=False)
    out = Path(a.out) if a.out else (paths.PLAIN / "calibration-report.md")
    report, senders, stats = calibrate.build_report(
        cat, manifest_path=Path(a.manifest) if a.manifest else None,
        census_path=Path(a.census) if a.census else None,
        sample_n=a.sample, top_senders=a.top, seed=a.seed, store_mode=paths.mode())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report)
    out.chmod(0o600)
    verdicts = out.with_name("sender-verdicts.csv")
    n = calibrate.write_verdicts_csv(verdicts, senders)
    verdicts.chmod(0o600)
    if a.json:
        Path(a.json).write_text(json.dumps(stats, indent=1))
    print(json.dumps(stats, indent=1), file=sys.stderr)
    print(f"\nreport   : {out}")
    print(f"verdicts : {verdicts}  ({n} senders — edit the `verdict` column)")
    print("Both files contain PII. They live in the store; do not commit them.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="life-index")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("consume", help="catalog files (default: the consume dir)")
    c.add_argument("path", nargs="?")
    c.add_argument("--source", default="manual", choices=("manual", "gmail", "drive"))
    c.add_argument("--keep", action="store_true", help="do not remove consumed files")
    c.add_argument("--harvest", action="store_true",
                   help="treat PATH as a Gmail harvest dir (meta.json per message)")
    c.set_defaults(fn=cmd_consume)

    s = sub.add_parser("search", help="full-text search the catalog")
    s.add_argument("query", nargs="+")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(fn=cmd_search)

    g = sub.add_parser("get", help="show one artifact as JSON")
    g.add_argument("sha256")
    g.set_defaults(fn=cmd_get)

    st = sub.add_parser("status", help="what the catalog holds")
    st.set_defaults(fn=cmd_status)

    cb = sub.add_parser("calibrate",
                        help="the harvest calibration report + sender-verdicts.csv")
    cb.add_argument("--manifest", help="harvest manifest.jsonl (fetch-side noise)")
    cb.add_argument("--census", help="sender-census.csv from mailctx.selectrun")
    cb.add_argument("--out", help="report path (default: <store>/calibration-report.md)")
    cb.add_argument("--json", help="also write the summary stats as JSON")
    cb.add_argument("--sample", type=int, default=30, help="random sample size")
    cb.add_argument("--top", type=int, default=60, help="senders shown in the report")
    cb.add_argument("--seed", type=int, default=0, help="sample seed (reproducible)")
    cb.set_defaults(fn=cmd_calibrate)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

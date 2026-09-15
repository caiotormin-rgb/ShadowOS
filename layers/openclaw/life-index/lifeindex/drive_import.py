"""CLI: catalog Drive documents from a JSON batch.

The Drive MCP is only callable by an agent, so fetching and cataloging are
split: an agent reads each file and writes a JSON array, this loads it.

  [{"file_id": "...", "title": "...", "mime": "...", "created": "2020-03-12",
    "doc_type": "property", "tier": 1, "text": "...", "note": "..."}]

Pass --no-text for documents that must be cataloged as pointers only.
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

from . import paths
from .drive import DriveDoc, import_doc
from .store import Catalog, apply_schema, connect


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="lifeindex.drive_import")
    ap.add_argument("batch", type=Path, help="JSON array of Drive documents")
    ap.add_argument("--no-text", action="store_true",
                    help="catalog metadata and link only; drop extracted text")
    a = ap.parse_args(argv)

    paths.require_store()
    conn = connect(paths.DB); apply_schema(conn)
    cat = Catalog(conn)
    run = cat.start_run(lane=0, model="drive-mcp", version="v1")

    docs = json.loads(a.batch.read_text())
    ok = 0
    for d in docs:
        doc = DriveDoc(file_id=d["file_id"], title=d["title"], mime=d.get("mime", ""),
                       created=d.get("created"), doc_type=d.get("doc_type"),
                       tier=d.get("tier"), note=d.get("note"),
                       text="" if a.no_text else d.get("text", ""))
        res = import_doc(doc, cat, run_id=run)
        ok += res["status"] == "ok"
        print(f"  {res['status']:10} T{res.get('tier','?')} {res.get('doc_type') or '-':11} "
              f"{res.get('chars',0):>7}ch {res.get('fields',0):>2}f  {d['title'][:44]}")
    cat.finish_run(run, ok)
    print(f"\n{ok} cataloged of {len(docs)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""CLI: refresh attachment hints, then report document candidates."""
import argparse, json, sys
from pathlib import Path

from mailctx.auth import AccessTokenProvider
from mailctx.gmail import GmailReadOnly
from mailctx.store import connect
from mailctx.sync import DB_PATH
from mailctx import targeting


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mailctx.targetrun")
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--report-only", action="store_true",
                    help="skip the API refresh; just list candidates already stored")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--out", type=Path, help="write candidates as TSV")
    a = ap.parse_args(argv)

    conn = connect(a.db)
    schema = (Path(__file__).resolve().parent.parent / "schema.sql")
    conn.executescript(schema.read_text())  # all IF NOT EXISTS; safe to re-apply

    if not a.report_only:
        state = conn.execute(
            "SELECT window_start_ts FROM mail_sync_state WHERE id=1").fetchone()
        api = GmailReadOnly(AccessTokenProvider())
        written = targeting.refresh_hints(
            api, conn, window_start_ts=state["window_start_ts"] if state else None)
        print("hints written:", json.dumps(written), file=sys.stderr)

    counts = dict(conn.execute(
        "SELECT hint, count(*) FROM mail_attachment_hints GROUP BY hint").fetchall())
    print("hint totals:", json.dumps(counts), file=sys.stderr)

    cands = targeting.document_candidates(conn, limit=a.limit)
    print(f"document candidates: {len(cands)}", file=sys.stderr)
    if a.out:
        with open(a.out, "w") as f:
            f.write("message_id\tdate\thints\tfrom\tsubject\n")
            import datetime as dt
            for c in cands:
                d = dt.datetime.fromtimestamp(c["internal_ts"], dt.UTC).date().isoformat()
                f.write("\t".join([c["message_id"], d, c["hints"] or "",
                                   (c["from_addr"] or "")[:45],
                                   (c["subject"] or "")[:80].replace("\t", " ")]) + "\n")
        print("wrote", a.out, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

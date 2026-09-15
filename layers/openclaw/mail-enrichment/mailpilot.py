#!/usr/bin/env python3
"""mailpilot — fetch a small set of full messages + attachments for the
life-index contents pilot.

Runs as the openclaw service account (that's where the token lives). Writes
one directory per message under --out: meta.json, body.txt (and body.html if
present), and every attachment under its original filename. Nothing touches
the metadata index. The output dir is a PILOT: plaintext, meant to be
reviewed and then shredded (or moved into the encrypted store when it exists).
Stdlib only.
"""
import argparse, base64, json, re, sys
from pathlib import Path

def b64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))

def safe_name(name: str, fallback: str) -> str:
    name = re.sub(r"[^\w.\- ()\[\]]", "_", name or "").strip() or fallback
    return name[:120]

def walk_parts(part, out: Path, api, message_id, counters):
    mime = part.get("mimeType", "")
    body = part.get("body", {})
    fname = part.get("filename") or ""
    if fname:  # an attachment
        if body.get("data"):
            data = b64(body["data"])
        elif body.get("attachmentId"):
            data = b64(api.attachment(message_id, body["attachmentId"])["data"])
        else:
            data = b""
        p = out / safe_name(fname, f"attachment-{counters['att']}")
        p.write_bytes(data); counters["att"] += 1
        print(f"    att  {p.name}  {len(data):,}B", file=sys.stderr)
    elif mime == "text/plain" and body.get("data"):
        with open(out / "body.txt", "ab") as f: f.write(b64(body["data"]) + b"\n")
    elif mime == "text/html" and body.get("data"):
        with open(out / "body.html", "ab") as f: f.write(b64(body["data"]))
    for sub in part.get("parts", []) or []:
        walk_parts(sub, out, api, message_id, counters)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True, help="TSV with message_id column (pilot-candidates.tsv)")
    ap.add_argument("--out", default="/tmp/mailpilot")
    a = ap.parse_args()
    from mailctx.auth import AccessTokenProvider
    from mailctx.gmail import GmailReadOnly
    api = GmailReadOnly(AccessTokenProvider())
    rows = [l.split("\t") for l in Path(a.ids).read_text().splitlines()[1:] if l.strip()]
    ok = err = 0
    for f in rows:
        cat, mid = f[0], f[1]
        out = Path(a.out) / f"{cat}-{mid}"
        out.mkdir(parents=True, exist_ok=True)
        try:
            msg = api.message_full(mid)
            headers = {h["name"].lower(): h["value"]
                       for h in msg.get("payload", {}).get("headers", [])}
            (out / "meta.json").write_text(json.dumps({
                "message_id": mid, "category": cat,
                "from": headers.get("from"), "to": headers.get("to"),
                "date": headers.get("date"), "subject": headers.get("subject")},
                indent=1, ensure_ascii=False))
            counters = {"att": 0}
            walk_parts(msg.get("payload", {}), out, api, mid, counters)
            ok += 1
            print(f"ok   {cat:9} {mid} atts={counters['att']}", file=sys.stderr)
        except Exception as e:
            err += 1
            print(f"ERR  {cat:9} {mid} {type(e).__name__}: {e}", file=sys.stderr)
    print(f"done: {ok} fetched, {err} errors -> {a.out}", file=sys.stderr)
    return 0 if err == 0 else 1

if __name__ == "__main__":
    sys.exit(main())

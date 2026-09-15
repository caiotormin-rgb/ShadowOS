"""Doctor search engine. JSON out; errors exit 2 with {"ok": false, "error": ...}.

Steps: new -> intake ... -> confirm-intake (queues the background search)
       -> run-jobs (timer; WhatsApps the requester when done) -> show --format shortlist
       -> choose -> show --format summary -> close
Email: verify-email -> confirm-email -> draft (code goes to the requester) -> ok
Timers: run-jobs, poll-notify, purge, mail-purge
"""

from __future__ import annotations

import argparse
import fcntl
import json
import sys
from pathlib import Path

import curate
import notify
import outreach
import render
import store


def out(value) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def parse_json_object(text: str) -> dict:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        raise store.DoctorError("bad_json", "--fields must be a JSON object.")
    if not isinstance(value, dict):
        raise store.DoctorError("bad_json", "--fields must be a JSON object.")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(store.DEFAULT_DB))
    sub = parser.add_subparsers(dest="command", required=True)

    def cmd(name, help_text, request=True):
        p = sub.add_parser(name, help=help_text)
        if request is not None:
            p.add_argument("--requester", required=True, help="authenticated sender; never from the model")
        if request:
            p.add_argument("--id", required=True)
        return p

    cmd("new", "start a request", request=False).add_argument("--lang", choices=["en", "pt"], required=True)
    cmd("list", "the requester's requests", request=False)
    p = cmd("intake", "set intake fields")
    p.add_argument("--fields", required=True, help="JSON object")
    p.add_argument("--format", choices=["json", "text"], default="json")
    cmd("confirm-intake", "requester confirmed the intake; queues the background search")
    cmd("search", "queue the search again, e.g. after it failed")
    cmd("widen", "search again with a new area").add_argument("--fields", required=True, help="JSON: zip, miles, max_miles")
    p = cmd("show", "request state")
    p.add_argument("--format", choices=["json", "intake", "shortlist", "summary"], default="json")
    cmd("choose", "requester picked from the shortlist").add_argument("--ranks", required=True, help="comma-separated")
    cmd("close", "close the request").add_argument("--reason", default="done")
    sub.add_parser("run-jobs", help="run queued searches (timer)").add_argument("--max", type=int, default=2)
    p = cmd("verify-email", "send a code to the requester's email", request=False)
    p.add_argument("--email", required=True)
    p.add_argument("--lang", choices=["en", "pt"], default="en")
    cmd("confirm-email", "check the code the requester received", request=False).add_argument("--code", required=True)
    p = cmd("draft", "draft an email to a chosen practice")
    p.add_argument("--rank", type=int, required=True)
    p.add_argument("--to", required=True)
    p.add_argument("--name", required=True, help="requester's name as signed in the email")
    p.add_argument("--cc", help="another household member's verified email")
    p.add_argument("--note", default="")
    cmd("approve", "requester typed the approval code").add_argument("--code", required=True)
    cmd("ok", "/ok <code>: approve and send, by the requester only", request=False).add_argument("--code", required=True)
    cmd("send", "send approved drafts")
    cmd("outreach", "status of this request's emails")
    sub.add_parser("poll", help="read new replies from the bot inbox")
    sub.add_parser("poll-notify", help="read new replies and tell requesters on WhatsApp")
    cmd("replies", "replies matched to this request")
    sub.add_parser("purge", help="apply 90-day retention")
    sub.add_parser("mail-purge", help="delete bot inbox mail older than 90 days")
    return parser


def dispatch(conn, a) -> None:
    if a.command == "new":
        out({"ok": True, "id": store.new_request(conn, a.requester, a.lang)})
    elif a.command == "list":
        out({"ok": True, "requests": store.list_requests(conn, a.requester)})
    elif a.command == "intake":
        result = store.set_intake(conn, a.id, a.requester, parse_json_object(a.fields))
        if a.format == "text":
            req = store.view(conn, a.id, a.requester)
            print(render.intake(req, req["lang"]))
        else:
            out({"ok": True, **result})
    elif a.command == "confirm-intake":
        store.confirm_intake(conn, a.id, a.requester)
        out({"ok": True, "step": "search", "status": "queued",
             "message": "The search runs in the background; the requester gets a WhatsApp message when it is ready."})
    elif a.command == "search":
        store.enqueue(conn, a.id, a.requester)
        out({"ok": True, "status": "queued"})
    elif a.command == "widen":
        store.reopen_search(conn, a.id, a.requester, parse_json_object(a.fields))
        out({"ok": True, "step": "search", "status": "queued"})
    elif a.command == "show":
        req = store.view(conn, a.id, a.requester)
        if a.format == "json":
            out({"ok": True, **req})
        else:
            print(getattr(render, a.format)(req, req["lang"]))
    elif a.command == "choose":
        try:
            ranks = [int(r) for r in a.ranks.split(",")]
        except ValueError:
            raise store.DoctorError("bad_choice", "--ranks must be numbers.")
        chosen = store.choose(conn, a.id, a.requester, ranks)
        out({"ok": True, "step": "summary", "chosen": [c["name"] for c in chosen]})
    elif a.command == "close":
        store.close(conn, a.id, a.requester, a.reason)
        out({"ok": True, "step": "closed"})
    elif a.command == "run-jobs":
        lock = open(Path(a.db).with_suffix(".jobs.lock"), "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            out({"ok": True, "skipped": "another run is active"})
            return
        out({"ok": True, "jobs": curate.run_pending(conn, notify.whatsapp, a.max)})
    elif a.command == "verify-email":
        outreach.start_verification(conn, a.requester, a.email, outreach.Mailer(), a.lang)
        out({"ok": True, "sent_code": True})
    elif a.command == "confirm-email":
        out({"ok": True, "verified": outreach.confirm_verification(conn, a.requester, a.code)})
    elif a.command == "draft":
        draft = outreach.draft(conn, a.id, a.requester, a.rank, a.to, a.name, a.cc, a.note)
        # The approval code goes to the requester directly, never back to the
        # agent: only the person can approve, by sending /ok <code>.
        try:
            notify.whatsapp(a.requester, render.draft_notice(
                draft, a.id, store.view(conn, a.id, a.requester)["lang"]))
        except store.DoctorError:
            outreach.cancel(conn, a.id, a.requester, draft["draft_id"])
            raise
        out({"ok": True, **{k: v for k, v in draft.items() if k != "approval_code"},
             "approval": "draft and code sent to the requester on WhatsApp"})
    elif a.command == "approve":
        out({"ok": True, "approved": outreach.approve(conn, a.id, a.requester, a.code)})
    elif a.command == "ok":
        draft_id = outreach.approve(conn, None, a.requester, a.code)
        request_id = conn.execute("SELECT request_id FROM outreach WHERE id=?", (draft_id,)).fetchone()[0]
        out({"ok": True, "request_id": request_id,
             "lang": store.view(conn, request_id, a.requester)["lang"],
             "results": outreach.send(conn, request_id, a.requester, outreach.Mailer())})
    elif a.command == "send":
        out({"ok": True, "results": outreach.send(conn, a.id, a.requester, outreach.Mailer())})
    elif a.command == "outreach":
        out({"ok": True, "emails": outreach.outreach_status(conn, a.id, a.requester)})
    elif a.command == "poll":
        out({"ok": True, **outreach.poll(conn, outreach.Mailer())})
    elif a.command == "poll-notify":
        result = outreach.poll(conn, outreach.Mailer())
        out({"ok": True, **result, "announced": outreach.announce(conn, notify.whatsapp)})
    elif a.command == "replies":
        out({"ok": True, "replies": outreach.replies(conn, a.id, a.requester)})
    elif a.command == "purge":
        out({"ok": True, "deleted": store.purge(conn)})
    elif a.command == "mail-purge":
        out({"ok": True, "deleted": outreach.mail_purge(outreach.Mailer())})


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    conn = store.connect(args.db)
    try:
        dispatch(conn, args)
    except store.DoctorError as e:
        out({"ok": False, "error": e.code, "message": str(e), **e.extra})
        return 2
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

# mail-context

A rebuildable local read model of Gmail metadata for the OpenClaw agent.

> **This is live** as of 2026-08-23: 132,588 messages over 18 years, synced
> twice daily, exposed to the OpenClaw agent as read-only MCP tools. (The banner
> here used to say the opposite; it was written before the layer was connected
> and was left behind.) The plan of record is
> [`docs/plans/00-mail-and-documents-MASTER.md`](../../../docs/plans/00-mail-and-documents-MASTER.md);
> operate it with `station/scripts/mailctl`.
>
> **Attachment targeting has never run.** Attachment presence is therefore
> `unknown` for every message — see below.

## Why stdlib only

torm has no system `pip` and no system `node`, and production runs as an
isolated `openclaw` account with no sudo and no dependency-install path. A
zero-dependency package can be deployed by copying a directory and audited by
reading it, so Gmail is reached over plain REST with `urllib.request` rather
than through `google-api-python-client`.

## Layout

| Path | What it is |
|---|---|
| `schema.sql` | The read model. No column stores a body or attachment content. |
| `mailctx/store.py` | Write side: idempotent upserts, tombstones, retention, sync cursor. |
| `mailctx/query.py` | The read-only surface handed to the agent. |
| `mailctx/attachments.py` | Attachment presence as `unknown \| yes \| no`. |
| `mailctx/preflight.py` | Gates that must pass before real mail is stored. |
| `tests/` | stdlib `unittest`, synthetic fixtures only. |

## Attachment presence is three-valued, and there is no boolean

`mail_messages.has_attachments` was removed in schema v3 (2026-08-25).
`store.migrate()` drops it from any database that still has it.

It was fed by the MIME walk in `sync.parse_message`, which never fires because
the sync fetches `format=metadata` — Gmail returns no parts. So the column read
`0` for all 132,588 indexed messages, and `mailq --attachments`, which filtered on
it, returned `nothing found` for a mailbox with eighteen years of attachments.
That is the worst kind of wrong: an answer, confidently given, with nothing to
show it was never checked.

The replacement is the `mail_message_attachments` view:

| state | means |
|---|---|
| `yes` | a targeting hint, or a stored MIME part, says a file is there |
| `no` | targeting covered this message and Gmail did not list it |
| `unknown` | nobody has ever asked Gmail about this message |

`unknown` is the default, and until a targeting run exists it is the answer for
every row. Coverage comes from `mail_targeting_runs`, which records what each run
asked for and how far it reached — an empty hints table used to mean either
"never asked" or "asked, found nothing", and callers took it for the second. Only
successful runs that refreshed the `any` hint count, only messages inside the
window they asked Gmail about, and only messages the index already held when the
run started. Everything else stays `unknown`.

`query.py` returns `attachment_state` per message and never the raw column;
`mailq --attachments` refuses to filter, with an explanation, until targeting has
run; `mailq --sql` refuses a query that mentions `has_attachments`. The rule
exists twice — as SQL for joinable queries and as Python for the per-instance
query path — and `tests/test_attachments.py` asserts the two agree on a fixture
matrix so they cannot drift.

Snapshots and the production database predate the migration and a snapshot is
opened `mode=ro`, so the views are overlaid as TEMP objects when they are
missing. Same SQL, any vintage.

## The two properties the design rests on

**Sending is absent, not merely unused.** `tests/test_no_send_path.py` greps the
package for send endpoints and for any OAuth scope outside `gmail.readonly` and
`gmail.compose`, and fails if one appears. Gmail's compose permission also
authorizes sending, so OAuth consent is not the boundary — this test is.

**Bodies are absent, not merely unread.** `tests/test_schema.py` inspects the
live schema for body/content-like columns and fails if one appears.

## Targeting and selection — feeding the document harvest

Two modules that decide which messages are worth fetching into the life-index
catalog. Neither stores a body, a filename, or a byte.

```bash
python3 -m mailctx.targetrun                       # populate mail_attachment_hints
                                                   # (operator: mailctl target)
python3 -m mailctx.selectrun --senders senders.csv --report-only   # the gate
python3 -m mailctx.selectrun --senders senders.csv \
        --out /tmp/harvest-candidates.tsv --census /tmp/sender-census.csv
```

`targeting.py` sources attachment **presence by shape** from Gmail's own search
index (`has:attachment filename:pdf` …), because `format=metadata` returns no
MIME parts and `format=full` would drag bodies into this layer. Ids only, ~a
dozen calls.

`selection.py` is cascade layers 1–2 (the master plan's numbering; layer 0 is
the listing-query exclusion `syncrun --exclude` already applies):

- layer 1 — phase-0 sender class `marketing` kills 43,950 of 64,200 (68.5%);
  an operator `never` verdict kills whatever it covers; an `always` verdict
  overrides the class, because a human using a campaign tool is misclassed by
  address shape and the correction has to win.
- layer 2 — no document-shaped attachment hint. "Has an attachment" is not
  "has a document".

Survivors are ranked (sender class, IMPORTANT/PERSONAL/SENT labels, hint
shape) so a run cut short has already brought back what matters. Ranking never
decides *whether* something is fetched.

`sender_rules` is where verdicts persist. The key is the **sender**: 41 senders
cover 50% of the non-marketing corpus, 154 cover 80%, only 2% of messages come
from a sender seen once — while subject-derived keys are 82% singletons. One
decision per sender, cached forever. `--report-only` prints the exact candidate
count and that coverage curve without an API call; the harvest handoff gates on
it.

## Preflight currently FAILS on torm

`mailctx.preflight` refuses the initial sync unless the index sits on an
encrypted filesystem. As of 2026-08-23 torm's root is plain ext4 on
`/dev/nvme0n1p5` with no LUKS layer and a plaintext swapfile:

```
[FAIL] encryption_at_rest: /dev/nvme0n1p5 is plaintext (part); 0700/0600 modes
       do not survive disk theft or offline access
[WARN] swap_encrypted: plaintext swap: /swap.img
```

This is a real gate, not a warning to click through. An operator may waive it
only by writing an override file with a reason, and the waiver is then reported
in every `status()` response so it cannot be quietly forgotten.

## Running the tests

```bash
cd layers/openclaw/mail-context && python3 -W error::DeprecationWarning -m unittest discover -s tests -t .
```

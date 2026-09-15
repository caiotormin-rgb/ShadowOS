# calendar-context

A rebuildable local read model of Google Calendar for the OpenClaw agent.

> **Nothing here is live.** No Google Cloud project, OAuth client, consent,
> token, database, timer, or agent tool has been created for Calendar. No
> network call has been made to the Calendar API and no real calendar data
> exists on this machine. The current OAuth grant on torm is `gmail.readonly`
> only, which authorizes nothing here.
>
> This directory is tracked *desired state* built ahead of the human-gated steps
> in [`docs/plans/openclaw-google-calendar-context.md`](../../../docs/plans/openclaw-google-calendar-context.md).

What **is** real: the package, the schema, and 186 unit tests that run offline
against synthetic fixtures. Everything they assert is asserted about this code,
not about a running system.

## The one thing to know

**V1 has no write path, and none is deferred to a later phase.** Not events, not
RSVPs, not reminders. Mail-context can create a Gmail draft; this cannot create
anything. That is enforced at four independent layers:

| Layer | Mechanism |
|---|---|
| OAuth grant | two Calendar **readonly** scopes; a write is not authorized at Google |
| HTTP client | `GET` is the only verb; anchored path allowlist; mutating-segment denylist |
| SQLite schema | no outbox, no draft table, nothing to write *from* |
| Tool surface | six read methods; no method named for a write verb |

`tests/test_no_write_path.py` asserts all four — the last one behaviourally, by
running a full sync through a recording transport and checking that every
request it received was a `GET`.

## Why stdlib only

torm has no system `pip` and no system `node`, and production runs as an
isolated `openclaw` account with no sudo and no dependency-install path. A
zero-dependency package can be deployed by copying a directory and audited by
reading it, so Google Calendar is reached over plain REST with `urllib.request`
rather than through `google-api-python-client`.

## Layout

| Path | What it is |
|---|---|
| `schema.sql` | The read model. No column stores a description, attachment, or conference secret. |
| `calctx/timespec.py` | The all-day vs. timed split, and the rolling-window arithmetic. |
| `calctx/gcal.py` | The `GET`-only transport. Three endpoints; there is no fourth. |
| `calctx/store.py` | Write side of *our own* index: idempotent upserts, per-calendar cursors, prune. |
| `calctx/sync.py` | Bounded initial load, `syncToken` incremental, `410` recovery, re-anchoring. |
| `calctx/query.py` | The read-only surface handed to the agent. |
| `calctx/logs.py` | The only module allowed to import `logging`; whitelists field names. |
| `calctx/auth.py` | Scope and token-file policy. The broker itself is mail-context's. |
| `calctx/syncrun.py` | What the systemd timer calls. |
| `calctx/authorize.py` | The one-time operator consent flow. |
| `systemd/` | Sync service and 30-minute timer, as tracked text. **Not installed.** |
| `tests/` | stdlib `unittest`, synthetic fixtures only. |

## It depends on mail-context being present

This package imports two modules from the sibling layer:

```python
from mailctx.preflight import run, require_for_initial_sync   # the encryption gate
from mailctx.auth import AccessTokenProvider, ClientConfig, TokenStore, authorize_interactive
```

`calctx/__init__.py` puts `../mail-context` on `sys.path` so that is a readable
one-liner rather than something a future reader discovers from an `ImportError`.
**If `layers/openclaw/mail-context/` is moved or removed, this package stops
importing.**

The coupling is deliberate and temporary. The encryption gate is not
mail-specific and should not exist twice; the OAuth broker is scope-
parameterized precisely so siblings can reuse it. A shared package extraction is
planned and has not been done — a second consumer (this one) is the trigger for
it, not the moment to attempt it.

One Desktop OAuth client is shared so the operator consents once. **A shared
client is not a shared capability**: this layer keeps its own token file
(`~/.config/calendar-context/calendar-token.json`), its own scope tuple, and its
own transport ceiling, so revoking one layer is not confusable with revoking the
other. `calctx.auth.scopes_are_readonly` re-checks the *stored* grant at use
time, because a token outlives the code that requested it.

Nothing here modifies anything under `mail-context/`.

## Scopes

```text
https://www.googleapis.com/auth/calendar.calendarlist.readonly
https://www.googleapis.com/auth/calendar.events.readonly
```

Not `calendar`, `calendar.events`, or `calendar.events.owned` — all three
authorize writes, which would make "no write path" a property of our code rather
than of the grant. Not the single `calendar.readonly` either: it is write-free
but also carries read access to Calendar settings and ACLs that V1 never
touches. The calendarList scope is not optional — `calendar.events.readonly`
does not authorize `calendarList.list`, without which the indexer could only
read a hardcoded `primary` and could not discover a calendar's IANA timezone.

If the granular calendarList scope is not offered on the consent screen, the
plan's only approved fallback is `calendar.readonly`
(`python3 -m calctx.authorize --fallback-scope`).

## The parts that are easy to get quietly wrong

**All-day events are not instants.** A birthday is a date, and it is the same
date in Auckland and in São Paulo. A timed row stores an epoch plus the IANA
zone Google returned; an all-day row stores `YYYY-MM-DD` and no epoch; a `CHECK`
constraint rejects a row that is both or neither. `order_ts` exists only so an
agenda can interleave the two in one `ORDER BY`, and `calctx/query.py` strips it
from every returned row.

**The window is bounded on both sides**: −3 months to +12 months. Twelve months
forward is the smallest horizon that materializes at least one instance of an
annual series. Three months back covers "when did I last see X" and matching an
appointment candidate extracted weeks ago. Mail-context's 12-months-*backward*
window is deliberately not mirrored: mail is an archive, a calendar is a
schedule.

**`events.list` forbids `timeMin`/`timeMax` together with `syncToken`.** A token
silently carries the bounds of the request that minted it, so a rolling window
drifts away from a fixed anchor and newly-in-range events at the far edge never
arrive. Each calendar's `anchor_start_ts`/`anchor_end_ts` are stored with its
token and compared on every run; drift beyond one month forces a bounded resync.
The client refuses the parameter combination outright.

**There is no RRULE engine here, and there should not be.** Recurrence masters
are stored with their `RRULE`/`RDATE`/`EXRULE`/`EXDATE` array verbatim so the
rule stays auditable, but expansion is Google's job via `events.instances`. A
hand-rolled expander over `BYSETPOS`, `BYDAY`, `UNTIL`, DST transitions, and
leap-day anchoring is the likeliest single source of a silently wrong agenda,
and it would be wrong in a way no synthetic fixture discovers.

**A cancelled occurrence is not an occurrence that never existed.** The two give
different answers to "am I free Thursday?" and to the appointment check, so
cancelled instances are stored with `status='cancelled'` rather than deleted.

## Preflight, and a caveat about the waiver

`calctx.syncrun` calls `mailctx.preflight.require_for_initial_sync` before it
can store anything. On torm the encryption-at-rest check fails on its own merits
— `/` is plain ext4 on `/dev/nvme0n1p5` with no LUKS layer and a plaintext
swapfile — and currently **passes only because an operator waiver file exists**
at `~/.config/mail-context/encryption-waiver.txt`. That waiver is surfaced in
every `status()` response so it cannot be quietly forgotten.

> **Operator decision needed.** That waiver was written for mail-context and
> scopes itself in its own text: *"The index holds Gmail metadata, subjects, and
> snippets… Revisit… immediately if… the retained field set widens beyond
> metadata and snippets."* Calendar-context stores event titles, locations, and
> attendee email addresses in a **separate** index. That is arguably the field-set
> widening the waiver asks to be revisited on. This package does not decide the
> question: it reads the existing waiver as written, and flags it here.

## Runtime paths (production, not created from here)

```text
/home/openclaw/.config/calendar-context/calendar-token.json   0600
/home/openclaw/.config/mail-context/client_secret.json        0600, shared client
/home/openclaw/.local/state/calendar-context/                 0700
└── calendar-context.sqlite                                   0600
```

Live OAuth files, SQLite files, WAL/SHM files, and calendar exports stay under
`/home/openclaw` and never enter this repository.

## Operating it

```bash
python3 -m calctx.authorize --port 8765     # one-time, as the owning account
python3 -m calctx.syncrun --preflight       # report the gate, change nothing
python3 -m calctx.syncrun --once            # one sync (initial if new)
python3 -m calctx.syncrun --forward 1 --back 0 --once   # bounded validation run
python3 -m calctx.syncrun --status          # counts, freshness, waiver
```

The systemd units under `systemd/` are tracked text and are **not installed by
any agent** — the `openclaw` account is unreachable without sudo, so installing
them is an operator step. The install commands are in the `.service` header.

## Running the tests

```bash
cd layers/openclaw/calendar-context && python3 -W error::DeprecationWarning -m unittest discover -s tests -t .
```

186 tests, no network, no credentials, synthetic fixtures only. Fixture
timestamps are derived from the window constants rather than from a fixed epoch,
so they cannot drift out of the rolling window as real time passes and make a
correct prune look like a bug.

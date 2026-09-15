# TODO — OpenClaw Google Calendar context

**Status:** TODO — scoped, not started, no code written
**Owner:** Caio
**Target runtime:** isolated `openclaw` account on `torm`
**Last scoped:** 2026-08-23

This is an implementation plan, not proof of live capability. No Google API,
OAuth client, calendar cache, service, timer, or agent tool described below has
been created or enabled. No Google Calendar credential exists on torm, no
network call has been made to Google, and no real calendar data has been read.

This plan is the sibling of
[`openclaw-google-mail-context.md`](openclaw-google-mail-context.md) and
deliberately reuses its idiom: Google stays authoritative, the local index is a
rebuildable read model, and the agent gets a typed read-only contract instead of
SQL. Read that plan first; this one only states where calendars differ.

## Decision

Google Calendar will remain the authoritative system. A rebuildable local SQLite
read model will become OpenClaw's calendar-context surface so ordinary "what is
on my calendar" lookups do not require repeated Google calls.

**V1 is read-only. There is no write path, and none is deferred to a later
phase of this plan.** Mail-context V1 has a narrow write path (it creates Gmail
drafts). Calendar-context V1 has none at all. That is the single most important
difference between the two plans and it is enforced at four independent layers:
the OAuth grant, the HTTP client, the SQLite schema, and the tool surface.

V1 will:

- index a bounded window of **−3 months to +12 months** across the calendars the
  user has selected in their own Calendar UI;
- store recurring **series** (verbatim `RRULE`/`RDATE`/`EXRULE`/`EXDATE`) *and*
  **materialized instances** inside that window, so a query never expands a
  recurrence rule at read time;
- store timed events as a UTC instant plus their original IANA timezone, and
  all-day events as calendar dates, with no coercion between the two;
- refresh every 30 minutes through incremental `syncToken` synchronization;
- recover from an expired `syncToken` (`410 GONE`) with a bounded full resync;
- report freshness in every response; and
- expose a read-only appointment-deduplication check so mail-context's
  `category='appointment'` candidates are not proposed for things already
  scheduled.

Corporate Google accounts, credentials, and calendars are outside this project.

## Product workflow

```text
Google Calendar API  (read-only scopes only)
   |
   | bounded initial sync + 30-minute syncToken sync
   v
SQLite calendar-context index
   |
   +--> series layer      (RRULE, EXDATE, overrides, cancellations)
   +--> instance layer    (materialized occurrences inside the window)
   |
   +--> read-only agenda / search / event / conflicts
   +--> appointment-match check
              ^
              |
              | "is this already scheduled?"
              |
        mail-context action_candidates (category='appointment')

There is no V1 write path. Not for events, not for RSVPs, not for reminders.
```

Normal calendar questions use SQLite first. A live Google fetch is required only
when:

- the requested instant is outside the −3/+12-month window;
- the index is stale, empty, or missing the requested calendar; or
- an operator explicitly asks for a live re-read to diagnose a discrepancy.

Because there is no write path, there is no "refresh live before mutating" step.
That entire class of race condition is absent from V1 by construction.

## V1 non-goals

- Creating, updating, moving, importing, or deleting any event.
- Responding to an invitation — accept, decline, tentative, or proposing a new
  time.
- Creating, changing, or dismissing reminders or notifications.
- Google Tasks, Keep, or Contacts.
- Changing calendar ACLs, sharing, visibility, or Calendar settings.
- Creating or joining conference links, or storing conference join secrets.
- Storing event descriptions, notes, agenda bodies, or attachments.
- Reading other people's free/busy beyond what an already-shared calendar
  exposes.
- Push notifications, `events.watch`, Pub/Sub, Tailscale Funnel, or any public
  webhook endpoint.
- Corporate or delegated calendars.
- A vector database or calendar-wide embeddings.
- Model calls inside the refresh timer.
- Backing up plaintext calendar context to Git, GitHub, cloud-sync folders, or
  the redacted OpenClaw state repository.
- Modifying anything under `layers/openclaw/mail-context/`. The integration in
  §"Appointment deduplication contract" is consumed *by* mail-context later, on
  its own schedule; calendar-context does not reach into it.

## Data boundary

### Runtime paths

Planned private runtime locations, mirroring mail-context:

```text
/home/openclaw/.config/calendar-context/       mode 0700; non-Git config
/home/openclaw/.local/state/calendar-context/  mode 0700; rebuildable state
└── calendar-context.sqlite                    mode 0600
```

OAuth material stays outside Git. The SQLite database is private but is not a
secret store; it must never contain OAuth tokens, Gateway tokens, or passwords.

`/home/openclaw/` is mode `0700` and is not readable from Caio's account. Every
path above is *tracked desired state* in this repo, never written from here.

### Scope decision

Request exactly two scopes:

```text
https://www.googleapis.com/auth/calendar.calendarlist.readonly
https://www.googleapis.com/auth/calendar.events.readonly
```

Justification, since this is the load-bearing safety decision:

- **Why not `calendar`, `calendar.events`, or `calendar.events.owned`.** All
  three authorize writes. Requesting any of them would make "no write path" a
  property of our code rather than of the grant, and mail-context already
  records why that is not good enough: Gmail compose permission can also
  authorize sending, so consent alone was rejected as the boundary there. Here
  we can do better — pick a grant under which a write is simply not authorized.
- **Why not the single `calendar.readonly`.** It is write-free and would work,
  but it is broader than needed: it also carries read access to Calendar
  settings and ACLs, which V1 never touches. The two granular readonly scopes
  are a strict subset of it.
- **Why `calendar.calendarlist.readonly` is needed at all.**
  `calendar.events.readonly` does not authorize `calendarList.list`. Without it
  the indexer could only ever read the hardcoded `primary` calendar and could
  not discover a calendar's IANA timezone. `calendarList.list` returns both
  `timeZone` and `selected` per entry, which is also why `calendars.get` is not
  needed and its scope is not requested.
- **Consequence.** Under this grant, a `POST`, `PUT`, `PATCH`, or `DELETE` to
  any Calendar endpoint fails at Google with `403 insufficient permissions`,
  including in the presence of a bug, a prompt injection, or a future careless
  edit. The grant is the outer wall; the client, schema, and tool surface are
  three inner walls.

If the granular `calendar.calendarlist.readonly` scope proves unavailable on the
consent screen at implementation time, the approved fallback is the single
`calendar.readonly`. Falling back to any non-`readonly` scope is not approved
and voids this plan.

### Retained event fields

Per calendar:

- calendar ID, summary, IANA timezone, `selected` flag, primary flag
- access role, recorded for auditing only and never consulted to decide whether
  to write, because V1 never writes
- last synchronized timestamp and deletion tombstone

Per event (series layer):

- event ID, iCalUID, etag, and `sequence`
- kind: `single`, `series`, or `override`
- `recurringEventId` and `originalStartTime` for overrides and cancellations
- status: `confirmed`, `tentative`, or `cancelled`
- summary (title) and location
- organizer and creator email
- the authenticated user's own response status
- transparency (`opaque`/`transparent`) and visibility
- start/end as either a UTC instant plus IANA timezone, or a calendar date
- the `recurrence` array, stored verbatim as received
- Google's `updated` timestamp and our last synchronized timestamp
- deletion tombstone

Per instance (materialized layer):

- instance ID, owning series event ID, calendar ID
- start/end in the same two-shape form as above
- status, and whether this instance is an override of the series
- summary, location, transparency, and own response status, denormalized —
  because an override may change the title or time of exactly one occurrence,
  and an agenda that reads the series row would show the wrong one

**Never stored:** event `description`, attachments, conference entry points or
join codes, working-location or focus-time payloads, extended properties, and
attendee comments. A test must assert against the live schema that no column
named for a description or body exists, mirroring the mail-context no-body rule.

Attendee emails and response statuses *are* stored in their own table, because
conflict detection has to know which invitations the user declined. They are
never logged.

### Timezone and all-day rule

This is where a plausible-looking implementation silently corrupts an agenda, so
it is a schema constraint rather than a convention:

- A **timed** event stores `start_utc` (epoch seconds) and `start_tz` (the IANA
  name Google returned, verbatim). Both are required.
- An **all-day** event stores `start_date` as `YYYY-MM-DD` and stores **no**
  epoch. A birthday is not an instant and must not be turned into one.
- A `CHECK` constraint rejects any row that has both or neither. Coercion is
  structurally impossible, not merely discouraged.
- An `order_ts` column exists **only** as a sort key so agendas can interleave
  the two kinds. For an all-day row it is local midnight in the owning
  calendar's timezone. It is documented in the schema as an ordering aid that
  must never be returned as an instant, and every returned row carries its
  `start_kind` so a caller cannot mistake one for the other.
- `zoneinfo` and the system tzdata provide the conversions. Both are present on
  torm and neither is a third-party dependency.

### Proposed SQLite entities

```text
cal_calendars       one row per calendar from calendarList
cal_events          series layer: singles, recurring masters, overrides
cal_attendees       attendee email + response status per event
cal_instances       materialized occurrences inside the window
cal_search          FTS5 index over instance summary and location
cal_calendar_sync   per-calendar syncToken, window anchor, status
cal_sync_state      global window bounds, freshness, status, error class
cal_sync_runs       counts, timings, and redacted failure evidence
```

Per-calendar sync rows are required because Google issues one `syncToken` per
calendar, not one per account.

### Typed read-only contract

The agent must not receive arbitrary SQL, a database path, or a filesystem
handle. Expose only:

```text
calendar_context.agenda(since, until, calendar_ids?, include_declined?)
calendar_context.search(query, since?, until?, calendar_ids?)
calendar_context.event(event_id, calendar_id?)
calendar_context.conflicts(since, until)
calendar_context.match_appointment(start, end?, all_day_date?, title_hint?,
                                   tolerance_minutes?)
calendar_context.status()
```

There must be no `create`, `insert`, `update`, `patch`, `delete`, `move`,
`import`, `quickAdd`, or `respond` operation anywhere in the V1 schema, and a
test must assert that no public method name on the contract matches those verbs.

The connection is opened `mode=ro` with `PRAGMA query_only = ON`, so read-only
is enforced by SQLite rather than by politeness. This matches
`mailctx.store.connect(read_only=True)`.

Every response carries a freshness record of the same shape as
`mailctx.query.Freshness` — `last_success_at`, `age_seconds`, `status`,
`error_class`, `is_stale`, `window_start_ts` — plus one added field,
`window_end_ts`, because a calendar window is bounded on both sides where a mail
window is not. Stale context may be returned, but never without naming its age.

## Window contract

The indexed window is **−3 months to +12 months** from the current sync.

- **+12 months forward** because a calendar is consulted forward. Twelve months
  is the smallest horizon that materializes at least one instance of an annual
  series — birthdays, renewals, anniversaries — which is exactly the class of
  event a shorter window would silently hide.
- **−3 months backward** because some backward reach is needed for "when did I
  last see X", for conflict checks against events that just passed, and for
  matching a mail-context appointment candidate extracted weeks ago against
  something already on the calendar. Three months is cheap and covers those.
- Mail-context's 12-months-backward window is deliberately *not* mirrored. Mail
  is an archive; a calendar is a schedule.

`window_start_ts` and `window_end_ts` are stored in sync state, recomputed on
each run, returned in every freshness record, and enforced on prune. Instances
outside the window are pruned; a series master is retained while it still has
any instance in the window.

### The window/token interaction

Google's `events.list` **forbids `timeMin`/`timeMax` together with
`syncToken`**. The token silently carries the bounds of the request that minted
it. A rolling window therefore drifts away from its token's fixed anchor: after
a month of incremental syncs, the token is still delivering changes for the
window as it stood a month ago, and newly-in-range events at the far edge never
arrive.

This is the single easiest way to build a calendar index that looks healthy and
is quietly wrong, so it must be handled explicitly:

- Record `anchor_start_ts` and `anchor_end_ts` alongside each calendar's
  `syncToken`, set to the bounds of the full sync that minted it.
- On every run, compare the desired window to the stored anchor.
- When drift exceeds one month, force a bounded full resync for that calendar to
  re-anchor, and record it as `kind='resync'`.
- A monthly re-anchor is also a cheap, self-healing correction for any
  accumulated incremental drift.

## Recurrence contract

Recurrence is the hard part of this project. The contract:

1. **Series layer.** Sync with `singleEvents=false`. Google returns recurring
   masters carrying the `recurrence` array, and returns single-instance
   overrides and cancellations as separate events keyed by `recurringEventId`
   plus `originalStartTime`. Store the `recurrence` array verbatim; do not
   normalize, re-serialize, or "clean up" an RRULE.
2. **Instance layer.** Materialize occurrences with the `events.instances`
   endpoint — a `GET` — bounded by `timeMin`/`timeMax` to the window. Google
   performs the expansion.
3. **V1 does not implement an RRULE engine.** A hand-rolled expander over
   `BYSETPOS`, `BYDAY`, `UNTIL` semantics, DST transitions, and leap-day
   anchoring is the most likely single source of silently wrong agendas in a
   stdlib-only package, and it would be wrong in a way no synthetic test
   discovers. Delegating expansion to Google is the correct trade here. The
   series layer exists so the rule is still *auditable* locally, not so we can
   re-derive it.
4. **Non-recurring events** get exactly one instance row derived locally. No API
   call.
5. **Cancelled instances are stored with `status='cancelled'`, not deleted.** A
   query must be able to distinguish "this occurrence was cancelled" from "this
   occurrence never existed" — the two produce different answers to "am I free
   Thursday?" and to the appointment-match check.
6. **Overrides are denormalized onto the instance row** (see retained fields).
   An agenda read must never have to consult the series row to learn that one
   occurrence moved.
7. **Re-materialization.** A series is re-materialized when the series row
   changes, when an override or cancellation for it arrives, or when the window
   moves. Materialization is a full replace of that series' instances inside the
   window, inside one transaction, so a partial expansion cannot leave orphans.

## Synchronization contract

1. Verify the preflight gate before storing any real calendar data (see
   §Security design). A failing gate stops the initial sync.
2. Enumerate calendars with `calendarList.list` and index only entries the user
   has marked `selected` in their own Calendar UI. That makes the human's
   existing choice the scope control rather than inventing a second one.
3. Run a bounded initial sync per calendar with explicit `timeMin`/`timeMax`,
   `singleEvents=false`, and `showDeleted=true`.
4. Apply pages idempotently, one transaction per page. A replayed page must be a
   no-op beyond `synced_at`.
5. `nextSyncToken` is returned only on the final page. Store it in a **separate
   transaction, after the data transaction for the final page has committed**,
   together with the window anchor. A crash before that point leaves the
   previous token in place and the next run replays — which is safe precisely
   because step 4 is idempotent.
6. Run incremental sync every 30 minutes under a systemd user timer, independent
   of the model and of Gateway conversation lifecycle. The timer makes no model
   call.
7. **If Google returns `410 GONE` for an expired `syncToken`, discard the token
   and run a bounded full resync for that calendar instead of failing
   permanently.** This is the exact analogue of Gmail's `404` history-cursor
   recovery in the mail plan.
8. Re-anchor a calendar whose window drift exceeds one month (see §Window
   contract).
9. Re-materialize affected series after the series layer for that calendar has
   been applied, never interleaved with it.
10. Prune instances outside the window and series masters with no remaining
    in-window instance. Retain short-lived deletion tombstones only as needed
    for reconciliation.
11. Preserve the last good index through authentication or network failure. A
    failed run records `status` and `error_class` and changes no event data.
12. Expose `last_success_at`, status, error class, and both window bounds to
    every consumer. Never present stale context as current without naming its
    age.

Per-calendar failure is isolated: one calendar returning `403` or `404` must not
abort the run for the others.

## Appointment deduplication contract

Mail-context produces `action_candidates` rows with `category='appointment'`.
Without a calendar check, the system will cheerfully propose an appointment the
user already scheduled. The integration point:

```text
calendar_context.match_appointment(start, end?, all_day_date?, title_hint?,
                                   tolerance_minutes?)
```

Rules:

1. **Read-only and side-effect free.** It answers a question. It does not create
   anything, does not mark the candidate, and does not write to either database.
2. **Calendar-context owns the interface; mail-context is not modified by this
   plan.** Wiring mail-context to call it is a separate, later change with its
   own record.
3. It takes either a timed `start` (with optional `end`) or an `all_day_date`,
   never a coerced blend of the two, matching the schema rule.
4. Matching is by time proximity first, within `tolerance_minutes` (default 60,
   since an emailed appointment time is frequently approximate), then optionally
   narrowed by a fuzzy `title_hint`.
5. It returns candidate instances with an explicit match reason and a confidence
   band, plus freshness. It returns evidence, not a verdict — the decision to
   suppress a candidate belongs to the caller.
6. Cancelled instances are returned flagged as cancelled and must not count as
   "already scheduled".
7. Declined invitations are likewise flagged rather than silently treated as
   attendance.
8. A stale index must never produce a confident "already scheduled" answer. When
   `is_stale` is true the result says so and the caller is expected to degrade
   to proposing rather than suppressing.

## Security design

- **Reuse the mail-context preflight gate. Do not reimplement it.** The indexer
  imports `run` and `require_for_initial_sync` from `mailctx.preflight` and
  calls the latter before the initial sync.
- **Note the coupling.** `mailctx.preflight` is currently the home of a check
  that is not mail-specific. It is expected to move to a shared package once a
  second consumer exists — this is that second consumer. Until it moves,
  calendar-context depends on the mail-context directory being present, and the
  README must say so plainly rather than let a future reader discover it from an
  ImportError.
- **The gate currently fails on torm, and that is the correct outcome.**
  Verified 2026-08-23: `/` is `/dev/nvme0n1p5`, plain `ext4`, `lsblk` reports
  TYPE `part` and not `crypt`; there is no LUKS layer; swap is `/swap.img`, a
  plaintext file on that same unencrypted filesystem. `check_encryption_at_rest`
  therefore returns `FAIL` and `check_swap_encrypted` returns `WARN`. **No real
  calendar data may be stored on torm until that is resolved.** Calendar-context
  must respect the gate, not route around it. The operator override file in
  `mailctx.preflight.run` is the only sanctioned waiver, and taking it is a
  deliberate, recorded human decision that then appears in every `status()`
  response.
- Use a personal Google Cloud project and a Desktop OAuth client. Confirm no
  corporate account is present in the chosen browser profile or OAuth project.
- Request only the two readonly scopes in §Scope decision.
- **Stdlib only, Python 3.12.** Talk to Google via raw REST over
  `urllib.request`. torm has no system `pip3` and no system `node`/`npm`, and
  the production runtime is an isolated account with no dependency-install path.
  A zero-dependency package can be installed by copying a directory and audited
  by reading it. This is a deliberate constraint, not an oversight.
- **The HTTP client issues `GET` only.** The request method is a constant, with
  a runtime guard that raises on anything else. The sole non-`GET` request
  anywhere in the package is the OAuth token refresh to the fixed
  `https://oauth2.googleapis.com/token` endpoint, which is a credential
  exchange, not a Calendar call.
- **Ship a no-write test, the analogue of mail-context's no-send test.** It must
  fail the build if any of the following appear: a write-capable HTTP verb
  directed at a `calendar/v3` URL; a non-`readonly` Calendar scope string; a
  write-capable Calendar endpoint fragment (`/events/import`, `/events/move`,
  `/events/quickAdd`, `/acl`, `/calendars` used for mutation); or a public
  method name on the API or query surface matching a write verb. It must also
  assert behaviourally, by recording every call a synthetic transport receives,
  that a full sync issues `GET` and nothing else.
- Treat all calendar text — titles, locations, attendee display names — as
  untrusted input. It never becomes a system instruction and cannot alter tool
  policy. Prompt injection in a meeting title is a realistic delivery vector
  precisely because anyone who can send an invitation can write one.
- Never interpolate calendar text into a shell command.
- Keep OAuth tokens out of prompts, logs, SQLite, and Git.
- **Log IDs, counts, durations, and error classes only.** Never event titles,
  attendees, locations, or descriptions. Enforce this with a logging helper that
  whitelists field names and raises on anything else, plus a test asserting no
  other module in the package imports `logging` directly.
- Run `station/scripts/scan-secrets.sh` before any commit or push.

## Planned repository layout

The expected tracked desired state:

```text
docs/plans/openclaw-google-calendar-context.md   this plan
docs/runbooks/openclaw-calendar-context.md       operations and recovery
layers/openclaw/calendar-context/                indexer/query implementation
layers/openclaw/calendar-context/systemd/        sync service and timer text
layers/openclaw/config/                          redacted config and schema
layers/openclaw/scripts/                         install/verify helpers
records/<date>-openclaw-calendar-context/        implementation evidence
```

**Nothing in `layers/openclaw/calendar-context/` exists yet.** No prototype was
written and none was left behind. As of 2026-08-23 the only artifact this plan
has produced is this file. When implementation starts, the systemd units are
tracked text under `calendar-context/systemd/` and are **not** installed by the
implementing agent — installing into the production account is an operator step,
because the `openclaw` account is unreachable without sudo.

Live OAuth files, environment files, SQLite files, WAL/SHM files, and calendar
exports stay under `/home/openclaw` and never enter this repository.

## TODO checklist

### 0. Reconcile prerequisites

- [ ] Resolve encryption at rest on torm, or record an explicit, dated operator
      waiver. Nothing below may store real calendar data until one of the two
      has happened.
- [ ] Re-run `mailctx.preflight.run` against the planned calendar state path and
      record the report verbatim.
- [ ] Run the operator-authenticated production OpenClaw health checks.
- [ ] Confirm the personal Google account boundary and that no corporate
      calendar is reachable from the chosen OAuth project.
- [ ] Decide whether `mailctx.preflight` moves to a shared package now or stays
      put with a documented cross-import.

### 1. Prove Google authentication in isolation

- [ ] Create a personal Google Cloud project and enable the Calendar API.
- [ ] Configure the OAuth consent screen and a Desktop client.
- [ ] Authorize exactly the two readonly scopes; capture the consent screen's
      rendered permission text as evidence that it grants no write.
- [ ] Verify with a single read call that the grant works, and with a single
      deliberate write attempt that Google rejects it `403`. Record both.
- [ ] Store token material outside Git with mode `0600`.
- [ ] Verify refresh-token durability from the same noninteractive service
      environment the timer will use.

### 2. Build the local context index

- [ ] Define and review the SQLite schema, including the `start_kind` CHECK
      constraint and the `order_ts` ordering-aid comment.
- [ ] Implement the read-only `GET`-only HTTP client with an injectable
      transport for tests.
- [ ] Implement bounded initial sync with `singleEvents=false` and
      `showDeleted=true`.
- [ ] Implement idempotent per-page application and post-commit token storage.
- [ ] Implement `410 GONE` recovery and window re-anchoring.
- [ ] Implement instance materialization via `events.instances`, including
      cancellations and overrides.
- [ ] Implement rolling-window prune.
- [ ] Implement FTS5 search and the typed read-only query contract with
      `mode=ro` plus `query_only`.
- [ ] Make freshness visible in every response.
- [ ] Add unit tests with synthetic fixtures only. Never put real calendar data
      in Git.

### 3. Prove the read-only boundary

- [ ] Add the no-write source-and-behaviour test described in §Security design.
- [ ] Add the no-description schema test.
- [ ] Add the logging-whitelist test.
- [ ] Verify direct, indirect, and prompt-injected attempts to create or modify
      an event all fail before any Calendar API call.
- [ ] Confirm the agent cannot obtain arbitrary SQL, the database path, the
      OAuth token, or general outbound HTTP merely to read calendar context.

### 4. Schedule and operate refresh

- [ ] Write the systemd user oneshot service and 30-minute timer as tracked
      text; hand installation to the operator.
- [ ] Add bounded timeouts, overlap prevention, transactional writes, and
      redacted logs.
- [ ] Verify restart, missed-run recovery, auth failure, network failure,
      expired-token recovery, and per-calendar failure isolation.
- [ ] Write the runbook: status, manual refresh, rebuild, revoke, safe cache
      removal.

### 5. Validate with real but bounded data

- [ ] Start with the primary calendar and a two-week window; compare indexed
      counts against the Calendar UI.
- [ ] Manually verify a recurring series with an override, a cancelled
      occurrence, an all-day event, and an event in a non-local timezone.
- [ ] Verify a DST transition does not shift a recurring local-time meeting.
- [ ] Confirm no descriptions, attachments, or conference secrets entered SQLite
      or the logs.
- [ ] Expand to the approved −3/+12-month window only after the above passes.

### 6. Wire the mail integration

- [ ] Implement `match_appointment` and test it against synthetic appointment
      candidates, including near-miss times, cancelled matches, declined
      matches, all-day matches, and the stale-index case.
- [ ] Only then, as a separate change with its own record, teach mail-context to
      consult it.

### 7. Launch and review

- [ ] Observe two weeks of agenda accuracy, recurrence correctness, sync
      freshness, and authentication stability.
- [ ] Keep every write capability out of V1.
- [ ] Decide separately, and only later, whether any calendar write path should
      ever exist.

## Acceptance criteria

V1 is ready only when all are true:

- The index can be deleted and rebuilt from Google without losing authoritative
  data.
- Sync is idempotent, exposes freshness, recovers from an expired `syncToken`,
  and re-anchors its window.
- A recurring series with overrides and cancellations reads back correctly, and
  no query expands an RRULE at read time.
- All-day events are never returned as instants, and timed events always carry
  their original IANA timezone.
- The −3/+12-month boundary and the no-description policy are verified against a
  bounded real sample.
- No calendar content appears in logs, Git, backups, or test fixtures.
- **Writing is absent from the OAuth grant, the HTTP client, the schema, and the
  tool surface, and is proven absent by an automated test.** A Calendar write
  attempted directly against the live grant returns `403`.
- `match_appointment` returns evidence and freshness, never a bare verdict, and
  degrades safely when the index is stale.
- The preflight gate is respected: either encryption at rest passes, or a dated
  operator waiver exists and is surfaced in every `status()` response.
- Production remains loopback-only, the isolated account boundary is preserved,
  and rollback instructions remain valid.

## Rollback and revocation

Rollback must be separately approved. Because V1 never wrote to Google, rollback
cannot damage calendar data — there is nothing of ours in the user's calendar to
undo. The safe sequence is:

1. disable the calendar-context timer and the local query tool;
2. revoke the Google OAuth grant;
3. preserve or delete the rebuildable SQLite cache according to the operator's
   explicit choice;
4. leave every Google Calendar event unchanged, which is automatic; and
5. record the result and verify the agent no longer has Calendar capability.

# The household tools

The tools that people other than me use. They live in the agent's own
workspace repo (160 commits, 2026-08-24 to 2026-09-14, all committed under
the agent's persona) and run inside the OpenClaw gateway as plugins. This
page describes them from that source,
their tests, and the commit trail. The source is under [`tools/`](../tools/).

```
tools/
  grocery-list/     core/ (Python engine, 402 tests)  plugin/ (TypeScript, 31)  skill/  bench/
  doctor-search/    core/ (Python, 33)                plugin/ (TypeScript, 11)
  access/           plugin/ (TypeScript, 17)          people, identities, grants
  household-router/ src/ (Node, 17)                   durable modes, domain ceiling
  household-config/ prompts + renderer (24)           generated per-domain instructions
  media-transcription/ bin/ (whisper.cpp wrappers)    voice notes and short video
automations/
  google-digest/         read-only morning brief
  calendar-ics-guardrail/ model-free .ics to calendar
```

Dependency direction is one way: skill → plugin → core. The core never
imports its plugin. The model never supplies an actor, a database path or a
confirmation; those come from the host and the operator's configuration.

## Grocery list

One live list per store. Items arrive as text, a voice note, a photo or a
short video; the agent interprets, the engine stores clean items with their
source and who added them. Portuguese and English are both first-class: the
same product merges across languages, replies come in the person's language,
and lists render grouped by store section in the walk order that person
configured.

- **Trips.** Closing a trip archives a dated snapshot, drops what was bought,
  and keeps what is missing on the next list. Close is retry-safe and can be
  reopened. Who closed it is recorded, recovered from the event log for old
  trips.
- **Scope.** Lists belong to a household with a member allowlist; the phone
  number is the identity everywhere. Access closes once the first member is
  enrolled. A per-requester private list is bound to one actor by hash and
  cannot be re-pointed.
- **Removal needs a human.** `grocery_remove` only previews. Only a typed
  `/remover CODE` (a native command the model cannot issue) executes it, with
  a single-use, expiring, requester-scoped confirmation that rejects a
  changed snapshot. Saying "yes" removes nothing.
- **Retention.** Message text and item notes are blanked after 90 days, in
  live and historical rows, every time the engine opens the list.
- **Seven narrow tools** (`grocery_show`, `grocery_add`, `grocery_mark`,
  `grocery_remove`, `grocery_trip`, `grocery_activity`,
  `grocery_preferences`) with schemas validated at execution. Subprocess
  diagnostics never reach the model.

8,500 lines of engine, 402 core tests, 31 plugin tests, a contract benchmark
that runs the real engine against synthetic households.

## Doctor finder

Does the legwork of finding an in-network provider for a household member
and, if asked, contacts the practice.

```
new -> intake ... -> confirm-intake      queues a background search
   -> run-jobs (timer, 2 min): curate -> shortlist to the requester on WhatsApp
   -> choose -> summary -> close
email: verify-email -> draft -> /ok CODE -> poll-notify (timer, 5 min)
```

- **Curation.** The model turns the request into search phrases; a web
  search near the requester's ZIP skips directories, insurers and job sites;
  each practice's landing page plus up to two insurance, services or contact
  pages are read; the model extracts facts and a 0–3 match with a supporting
  quote; anything under 2 is dropped; distance comes from public ZIP
  centroids; ratings from search snippets, never scraped review sites; top 8
  kept. Results are cached for seven days because the searches are slow.
- **Isolation.** Only the requester can see their request. Intake keeps the
  plan name only: no member ID, date of birth or diagnosis. The research
  model is pinned independently of the chat model, so changing how the
  household bot talks cannot change how research is done.
- **Outreach.** Email goes from a dedicated agent mailbox. Before anything
  is sent, the requester's own address is verified by code, the draft and a
  code are shown on WhatsApp, and only `/ok CODE` sends; a changed draft is
  not sent. The To address must be on the practice's own domain, CC must be
  a verified household address, STOP suppresses, and there are caps of ten
  emails per request and thirty per day. Replies are matched by request id
  and wrapped as untrusted text.
- **Lifecycle.** Three systemd user timers: jobs every two minutes, mail
  every five, retention daily; everything is purged 90 days after close.

The tool choice was made after a hands-on evaluation of the alternatives
(insurer directory APIs, provider registries, review sites, booking
detectors) with synthetic patients and a local test mail server. The
evaluation is what pushed the design to web-first curation: directory data
was slow, stale and missing the plan; public practice emails were rare;
"accepting new patients" was true for every record and therefore useless.

## Access and modes

**Household Access** holds people, identities and grants in a small store
and registers a trusted tool policy with the gateway. It started in monitor
mode (log what would be denied) and moved to enforcement for the shared
agent once the logs were clean. Missing sender, unknown person, inactive
grant, unmapped tool or unreadable store all deny. `/access who` and `/me`
are owner and member commands. Native approvals (`/remover`, `/ok`) re-check
the grant at approval time.

**Household modes** keep a durable Grocery or Doctor mode per authenticated
sender and account. A standalone word (`lista`, `groceries`, `médico`,
`doctor`) or the older slash aliases switch; a word inside a sentence does
not. The switch is acknowledged by a native handler with no model call.
While a mode is active the other domain's tools are blocked. Switching never
approves a pending confirmation.

## Media capture

Voice notes are transcribed locally with `whisper.cpp`: a small model as the
first pass, a larger one only for a language retry on notes over six seconds
when a slot is free, at most two transcriptions at once, per-chunk language
detection on long notes, and cleaning that strips non-speech tags without
eating real words. Failures are loud and exit non-zero so the gateway moves
on. Short videos get a 15-second gate, frame sampling that skips
decode-heavy inputs, and a capped, keyless vision pass. Transcripts and model
output are boxed as untrusted and look-alike control markers are folded
before neutralising.

## Automations

The **morning brief** reads Gmail, Calendar and Drive through a read-only
CLI wrapper and produces exception detection, not a summary: decisions and
actions, with uncertain inferences labelled. Google content is treated as
untrusted data, never as instructions. Local writes are limited to two state
files. A separate write policy says capability is not authorisation: any
draft, send, RSVP, event or Drive change is previewed and confirmed per
action.

The **calendar guardrail** replaces a model-driven automation with a
model-free script: every fifteen minutes it scans recent `.ics` attachments
and ensures the events exist on the primary calendar. Attendees are never
copied, updates are never sent, ICS text is passed as argv values and never
as shell, and cancellations or ambiguous recurrence are reported rather than
guessed.

## Model evaluation

The household chat model is a cloud model pinned by evidence, not by
default. A synthetic contract harness runs the real grocery engine, the
production tool schemas and the canonical skill against fourteen scenarios
in isolated fixtures, with bounded calls and no retries. A lighter model
passed 12 of 14 automatically and 14 of 14 after a manual review of two
correct refusals; a lighter-still model passed 9 and mishandled a request
for another person's private list. Decision: keep the current model, trial
the first in a restricted runtime, do not promote either on one repetition.

## The commit trail

Four bursts: a scaffold on 2026-08-24, the grocery engine on 2026-08-30 (42
commits), the hardening day on 2026-09-13 (93), and the refactor with access,
doctor search and the router on 2026-09-14 (23). Subjects state the defect
or the rule, not the diff. A selection:

```
2026-08-30  Refuse ambiguous item names instead of guessing a unit
2026-08-30  Assume and state instead of asking, where mistakes are cheap
2026-08-30  Make close retry-safe and add reopen
2026-08-30  Merge the same product across languages
2026-08-30  Scope lists to households with member allowlists
2026-08-30  Fix the due action, which failed on every call
2026-08-30  Fix the blank dedup key, and write down the trust boundary
2026-08-30  Make the tree carry the evidence, not our memories
2026-09-13  Give each person a timezone, and never show a nameless phone
2026-09-13  Confirm doubtful voice transcripts before adding items
2026-09-13  Serialize the schema upgrade so concurrent first opens cannot fail
2026-09-13  Refuse dates outside 1970-2100 instead of crashing on them
2026-09-13  Fail instead of silently dropping VAD when its model is missing
2026-09-13  Fail safe when the vision quota ledger cannot be updated
2026-09-13  Box transcript and model output as untrusted and neutralise spoofed markers
2026-09-13  Serialize item writes so simultaneous add and buy of a new item both succeed
2026-09-13  Use fictional phone numbers in tests instead of household numbers
2026-09-13  Guard tracked files against real and non-fictional phone numbers
2026-09-13  Blank message text after 90 days whenever the engine opens the list
2026-09-13  Keep purging the other databases when one fails
2026-09-14  Add compact Grocery member API with scoped confirmations and atomic retries
2026-09-14  Recover stale Grocery tools without mode loops and allow conversational replies
2026-09-14  Authorize household discovery wrappers with exact target grant checks
2026-09-14  Allow standalone household mode words without slash commands
```

Every deployment on the hardening and refactor days carried a backup path
and a written rollback that restores the paired files together, and a
validation log with the exact commands and counts.

# Trust boundary

The scoping audit (`AUDIT-scoping.md`) found several commands that check no
caller: `group`, `who`, `allow`, `members`. Whether that matters depends
entirely on who can reach them, which is a question about the plugin rather
than the CLI. This file answers it, because the audit deliberately did not
guess.

## Two populations

**Household members over WhatsApp.** They reach exactly one tool,
`grocery_list`, whose action list is a closed enum of fourteen:

    add, list, buy, unbuy, remove, close, reopen, history, stores,
    due, layout, help, onboard, activity

`activity` is the fourteenth, added deliberately — see *The activity action*
below for why it is safe to offer and what pins that down.

`group`, `who`, `allow`, `members`, `share` and `delivered` are **not in that
enum, and no code path in the plugin constructs them**.

Two suites assert this, in two languages, because the property has two halves
and each suite guards one:

- `tools/grocery-list/plugin/src/index.test.ts` checks the **enum at the source**: the
  action list the tool offers contains none of the operator-only commands. Run
  it with `npm test` in `tools/grocery-list/plugin/`.
- `test_plugin_contract.py` checks the **reach**: it reads `index.ts` as text
  and asserts that no action is named for an operator-only command, that no
  action's case builds argv invoking one, and that the set of subcommands the
  plugin can invoke is still exactly the fourteen pinned in that file. Run it
  with `python3 -m unittest discover -s grocery-list`.

The second is not a duplicate of the first. An action named innocently whose
case pushes `share` passes the enum check and fails the reach check; an action
added to the enum with no case fails the name check and never reaches the reach
check at all.

**Neither command runs the other's tests.** A green Python suite says nothing
about the vitest assertions, and the reverse. Check both before trusting this
section — the gap that made this paragraph necessary was not a missing test, it
was two suites and an assumption that green in one meant green in both. The agent serving that number also has no shell:
its OpenClaw config sets `tools.allow: ["grocery_list"]`, which the schema
defines as an absolute allowlist replacing the profile.

**The operator, on the machine.** Anyone with a shell in this directory can run
every subcommand — and can also open `data/grocery.sqlite3` with `sqlite3` and
read or rewrite anything. A gate on `group add` would not change what that
person can do; it would only make the CLI a slower way of doing it.

## What that means for the findings

The ungated administrative commands are **operator-only by design**, not an
oversight. They are not reachable by a household member, and against someone
with shell access they defend nothing that the database file does not already
concede.

They are still worth gating if either of these becomes true, and both are worth
watching for:

- **A new plugin action is added.** The enum is the boundary. Extending it
  without thinking is how an administrative command becomes reachable.
- **A second household shares this database.** Today all four members are in
  one group, so there is no other household to reach. The audit's findings
  about reading another household's contacts or rewriting their language
  preferences need a second household to exist before they mean anything.

## What is genuinely reachable from WhatsApp

Only the fourteen actions above. Everything reachable that way is scoped by the
caller's household, which is resolved from their phone number by the gateway
and passed as `--actor` on every action — the member cannot supply or spoof it,
because the plugin overwrites it with the authenticated sender.

## The activity action

`activity` reads the event log: who added, bought, removed, closed or reopened
what, and when. It was added to answer members' own questions ("quem comprou o
leite?"), and it widens what a member can see, so it was checked against the
two risks this file names rather than slipped in.

- **It reaches no administrative command.** It invokes `activity` and nothing
  else; `REACHABLE` in `test_plugin_contract.py` was widened by exactly that one
  name, and both reach and name checks still hold.
- **It only reads.** `test_read_only_actions_change_nothing` runs it as the
  plugin sends it and compares every table before and after.
- **One household.** Every query is keyed on the caller's group id, resolved
  from `--actor` as for every other action. `--by`, the only argument that names
  a person, resolves among that household's members and its own log, so a
  neighbour's name or number matches nobody (`test_activity.IsolationTests`).
- **No phone numbers.** People are printed by display name, else the last four
  digits (`•••0021`) — in the text and in the JSON (`people.public_label`,
  asserted in both suites). The member-facing `history` follows the same rule
  for who closed a trip.
- **Who sees what inside the household** is a policy, not a boundary: today
  every member sees all of the household's activity, consistent with onboarding
  telling everyone the list is shared and attributed. That decision lives in one
  function, `activity.visible_actors`, so narrowing it is a one-place change.
- **Its free-text filters cannot become flags.** The plugin refuses a `since`,
  `until`, `by` or `item` that begins with `-`, so a value cannot be read by
  argparse as an option.

`buy`, `unbuy` and `remove` take the item names as positional arguments,
followed by flags. The plugin accepts those names from `names`, `items[].name`
or `item` (the first that holds a real name; placeholder filler such as
`item` or `x` is dropped and never acted on), and refuses the call if any name
that would reach the engine begins with `-`, whichever field it came from.
Refusing was chosen over a `--` separator: the names precede the other flags,
and a separator would have to move all of them. A dash inside a name
(`pão-de-queijo`) is unaffected.

The same change made `buy`, `unbuy` and `remove` pass `sourceType`, `sourceRef`
and `rawText` through to the engine, as `add` already did. That adds flags to
existing subcommands; it does not make anything new reachable.

Per-person timezone overrides are `who timezone`, which is operator-only like
the rest of `who`.

## Message text retention

Owner decision (2026-09-13): the words a change came from — a typed message,
or a voice or video transcript — are kept for 90 days, then purged, and so is
anything history copied from them. The change itself (who, what, how many,
when) is history and stays.

| Where | What it holds | Rule |
| --- | --- | --- |
| `events.raw_text` | the message or transcript behind add/merge/buy/unbuy/remove | blanked 90 days after `occurred_at` |
| `item_sources.raw_text` | the same text, as an item's provenance | blanked 90 days after `observed_at` |
| `events.note` | the item's note as it was when the change was logged | blanked 90 days after `occurred_at`, every row |
| `trip_items.note` | the item's note when the trip closed | blanked 90 days after the trip's `closed_at` |
| `items.note` | the note on the live list, which members see and change | kept while the item is listed |
| `source_type`, `source_ref` | a category, and a pointer (message id, media file name) | kept: no content |
| names of items, stores, people; `contacts` | the list's vocabulary and the share allowlist | kept |

**Notes are not trusted to be short.** The skills used to tell the agent to put
the member's raw phrase for an unclear quantity in `note` (QA H1), and a
member can still dictate one. Only `items.note` — the live list — is exempt:
removing a note from something still on the list would change the list. Every
copy history made of a note goes on the same clock as the message text. The
skills now say the words go in `rawText` and `note` gets at most a short hint.

The engine writes no log files. Details and the reasoning are in `retention.py`.

**How it runs.** Every engine open (`db.connect`) probes partial indexes for
text past the cutoff and writes only when some is found, in one `BEGIN
IMMEDIATE` transaction; `scripts/purge_raw_text.py` does the same on demand or
from cron and reports counts only.

**The retention setting fails closed.** `GROCERY_RAW_TEXT_RETENTION_DAYS`
overrides the period with whole days, 1–3650. Any other value — `90d`, `0`, a
typo — makes *every* engine command exit with an error naming the variable,
including reads, until it is fixed or unset. That is deliberate: a mistyped
shorter period must not silently keep text for 90 days, or longer.

**Not covered here.** The original voice notes, photos and videos live in
OpenClaw's media store (`~/.openclaw/media/inbound/`), and the agent's session
history holds the transcript too; both follow OpenClaw's retention settings,
not this engine's.

**Copies of the database are not purged.** Each keeps whatever text it was
taken with. What happens to them is the owner's deletion decision, not this
purge's; the purge only reaches databases it is pointed at. Known copies on
2026-09-13:

| Location | What |
| --- | --- |
| `tools/grocery-list/core/data/grocery.sqlite3.bak-*` | pre-migration backups of the family list |
| `tools/grocery-list/core/data/dev.sqlite3` | the development scratch list |
| `~/backups/2026-09-13-plan/grocery.sqlite3` | family list, taken before planning |
| `~/backups/2026-09-13-deploy/grocery.sqlite3.pre-*` | family list, taken at each deploy step |
| `~/backups/2026-09-13-pre-2026.9.4-openclaw-dir.tar.gz` | archive holding the four `data/` files above |
| `~/backups/2026-09-13T03-37-00…-openclaw-backup.tar.gz` | archive holding the same four `data/` files |

Any new backup of `data/` joins this list. The live private per-requester
lists (the plugin's `privateDbDir`, one `<hash>.sqlite3` each) are live data,
not copies: the engine purges each one as it opens, and the script reaches
them all with `--private-dir`.

The one member-reachable bug the audit found was in `buy` on an unlisted item,
which stored an empty cross-language dedup key. Fixed, with a repair for rows
already written that way.

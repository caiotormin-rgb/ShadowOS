# Household scoping audit

Read-only audit of every SQL query in the engine, asking one question of each:
**can this return, count, or act on a row belonging to a household other than
the caller's?**

- Audited at `f92fcc45` (186 tests green, tree clean).
- Every finding below was reproduced on a throwaway `tempfile` database. The
  live list, the gateway, `config/members.json` and `tools/grocery-list/plugin/` were not
  touched. Nothing was fixed — this is the map, not the repair.
- The six previously-known instances are **regression-checked as fixed** at the
  bottom; the fourteen below are new.

## Verdict

Six instances were found by chasing one shape: *a query keyed on a store name
instead of a group id*. That shape is now, as far as I can tell, exhausted —
I found no seventh. But the count kept climbing because the shape was the
symptom, not the cause. There are **two more shapes**, and they are worse:

1. **Tables with no `group_id` column at all.** `contacts` and `shares` were
   never scoped. No amount of passing `group_id` into a query fixes a table
   that has nowhere to put it.
2. **Commands with no caller.** Five command families accept no `--actor`
   whatsoever, so the membership gate at `cli.py:270` cannot apply to them and
   explicitly exempts them. Everything the gate protects can be undone through
   them.

Shape 2 subsumes the entire class. **A stranger can enroll themselves as owner
of any household with one command** (S1). Once that is possible, the six
carefully-fixed leaks are not the way in — nobody needs a store-name collision
to read another household's list if they can simply join it. I would fix S1
before anything else on this page.

---

## S1 — The `group` command family has no caller, so anyone can join or empty any household

**`cli.py:270-271`**, with **`cli.py:161-174`** (parser) and **`groups.py:38-68`**.

```python
if args.command not in {"init", "group", "who", "allow", "members",
                        "help", "ajuda", "onboard"}:
    caller_group = group_for_actor(conn, getattr(args, "actor", None))["id"]
```

`group add` takes the actor being *added* as a positional argument. There is no
flag for the actor doing the *adding*. The gate is not bypassed so much as
inapplicable: there is no caller to check.

**Scenario.** Households A (`+1111111111`) and B (`+2222222222`) share nothing.
A stranger `+9999999999`, in neither:

```
$ group add +9999999999 --group "House A" --role owner
{"group": "House A", "actor": "+9999999999", "role": "owner"}
$ list --store Costco --actor +9999999999
{"store": "Costco", "items": [{"name": "Caviar", ...}]}      # A's list

$ group remove +1111111111 --group "House A"
{"removed": "+1111111111", "group": "House A"}
$ list --store Costco --actor +1111111111
error: unknown store: Costco                                  # A locked out
```

Both verified. `members sync --prune` (`cli.py:428`) is a second ungated path to
the same place: a roster file can rewrite any household's membership wholesale.

**Exploitable today**, and it does not need a second household to be damaging —
the eviction half works against the only household that exists.

**Judgment.** This may well be deliberate: these read as operator commands, run
by whoever owns the box. If so the trust boundary needs writing down, because
the code went to the trouble of building a membership gate and then left
administration outside it. Whether it is reachable by a WhatsApp requester is a
question about `tools/grocery-list/plugin/`, which is outside my scope — I did not look.
**Someone should answer that before this is called low-risk.**

## S2 — One household can silently redirect another household's shares to a number it chooses

**`contacts.py:14-34`** — `contacts.normalized_alias` is globally `UNIQUE`
(`db.py:98-106`), and `add_contact` is `ON CONFLICT DO UPDATE`.

```sql
INSERT INTO contacts(alias, normalized_alias, channel, target, added_at, active)
VALUES (?, ?, ?, ?, ?, 1)
ON CONFLICT(normalized_alias) DO UPDATE SET target = excluded.target, ...
```

**Scenario.** A allowlists their neighbour: `allow add pal --target +3333333333`.
B — or anyone — runs `allow add pal --target +9999999999`. There is no second
row; A's row is overwritten in place, keeping its alias and `added_at`. A's next
share is addressed to the new number:

```
A's allowlist now: [{'alias': 'Pal', 'target': '+9999999999', ...}]
A's next share is addressed to: +9999999999
```

Verified. This is worse than a read leak: `prepare_share` was fixed to render
only the caller's own items, and this redirects those correctly-scoped items to
an attacker-chosen destination. The allowlist is the control that makes sharing
safe, and it is writable by anyone, with no record that it changed hands.

**Exploitable today.** Does not require a second household — only a second
person with CLI reach.

## S3 — `contacts` and `shares` are not scoped at all

**`db.py:98-114`** (schema), **`cli.py:515`** (`allow list`), **`contacts.py:38-45`**
(`remove_contact`).

Neither table has a `group_id`. `mark_delivered` was given a group filter in
`94272dda` by joining `shares → stores`, which works, but it is the only place
that does. `allow list` returns every household's contacts, with names, channels
and phone numbers:

```
B sees A's allowlisted contacts:
[{'alias': 'Pal', 'channel': 'whatsapp', 'target': '+3333333333', ...}]
```

`remove_contact` will likewise deactivate any household's contact by alias.

**Exploitable today.** This is the root of S2 — S2 cannot be fixed by passing an
argument, only by giving the table an owner.

## S4 — `who set` is ungated: anyone can rewrite anyone's language and default store

**`cli.py:434`** → **`people.py:29-41`**. `who` is on the exemption list and,
like `group`, takes the subject as a positional with no caller flag.

```
B runs: who set +1111111111 --lang pt --store Nowhere
A's next reply: "Costco — 1 na lista · atualizada agora mesmo por Alice"
```

Verified — A's replies switched to Portuguese. The `default_store` half is the
sharper edge: `resolve_store` (`stores.py:35`) trusts the stored default without
checking it belongs to the caller's group, and `add` calls `store_row(...,
create=True)`, so a poisoned default silently **creates a new store in the
victim's household** and files their items into it. It cannot write into
*another* group — the group id is still the caller's — but it does let a third
party choose where someone else's groceries land.

**Exploitable today.**

## S5 — `who list` prints every household's identifiers

**`cli.py:436-437`** — `SELECT * FROM people ORDER BY actor`, unfiltered.

```
[{'actor': '+1111111111', 'display_name': 'Alice', ...},
 {'actor': '+2222222222', 'display_name': 'Bob',   ...}]
```

This is the same disclosure that made `last_touched` worth ranking high in
`94272dda` — a phone number, not an item — except here it is the whole roster at
once rather than one name in a footer. Fixing the footer and leaving this is
inconsistent.

**Exploitable today.** See S12 for the judgment on the `people` table generally:
the table being global is defensible; dumping it to any caller is not.

## S6 — The web history view shows every household's entire event log

**`webtest.py:432-441`**. The `events` query is built with no `group_id` clause.
When no store is selected `where` is empty, and the page renders the raw table:

```python
where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
rows = conn.execute(f"SELECT id, occurred_at, store, item_name, ..., actor, ...
                      FROM events {where} ORDER BY occurred_at DESC LIMIT ?")
```

The heading says so plainly: *"N most recent events (all stores)"* — across all
households, with `actor` (phone numbers), item names and notes. This is exactly
the leak fixed in `cli.py:478` for the CLI `events` command, still open in the
web surface.

**Severity capped by reachability**, not by scope: `webtest.py` binds to
`127.0.0.1`, has no authentication, and says of itself *"a test tool, not a
product surface"*. Anyone who can reach it can already read the database file.
I would not call this a disclosure so much as a **trap**: `ae0002aa` scoped this
file's footer, which will read to the next person as though the web UI is
scoped. It is not (see S7).

## S7 — The whole web UI is pinned to whichever household is first in the table

**`webtest.py:223`** (`toolbar`), **`427`** (`store_row`), **`540`** and **`614`**
(`resolve_store`), **`320`** (`current_items`), **`598-599`** (layout update).

`webtest.py` never calls `group_for_actor`. Its `actor` is a free-text form field
— *"Acting as (optional)"* — used only as the event-log author string. Every data
path therefore omits `group_id` and falls through to `default_group_id()`
(`stores.py:66-70`), i.e. `SELECT id FROM groups ORDER BY id LIMIT 1`.

```python
stores = conn.execute("SELECT name FROM stores ORDER BY normalized_name")  # :223
```

The store picker lists every household's stores; opening any of them shows group
1's list under that name; and the write paths (`/add`, `/buy`, `/close`,
`/store`) act on group 1 regardless of who is "acting as". The `last_touched`
call at `:348` passes `store_data["group_id"]`, which is self-consistent but
inherits the already-unscoped lookup — the footer is honest about a store that
was chosen wrongly.

**Exploitable today** by anyone at the keyboard, which is the same set of people
who can read the file. Worth fixing as correctness, and worth a comment saying
the tool is single-household, so the next reader does not trust it.

## S8 — Someone in two households loses access to one of them

**`groups.py:98-107`** — `ORDER BY g.id LIMIT 1`.

`group_for_actor` returns the *lowest-numbered* group the actor belongs to, and
no read command has a `--group` flag to say otherwise.

```
B is added to House A as well as House B.
B's stores:  ['Costco']          # House A's
B's list:    ['Caviar']          # House A's
```

Verified. B cannot reach House B's list at all any more — not a leak (B is a
legitimate member of A) but a silent lockout, and the natural configuration for
anyone who shops for two households. The docstring explains the empty-deployment
fallback carefully and says nothing about this tie-break.

**Exploitable today**, and likely to be hit by accident rather than malice.

## S9 — Household enumeration

**`cli.py:413-414`** (`group list`) and **`cli.py:406-408`** (`group members
--group X`), both ungated per S1.

```
group list    -> [{'id': 1, 'name': 'Household'}, {'name': 'House A'}, {'name': 'House B'}]
group members --group "House A"
              -> {'members': [{'actor': '+1111111111', 'role': 'owner', 'name': 'Alice', ...}]}
```

Any household's roster — phone numbers, display names, roles — is readable by
anyone. Subsumed by S1 (you could just join instead), but listed separately
because it is a pure read and would survive a fix that only guarded writes.

## S10 — Every scoped helper defaults to unscoped, and the façade re-exports them that way

**`grocery.py:11-34`**, and the signatures in `items.py:57`, `insights.py:26`,
`insights.py:74`, `insights.py:100`, `render.py:17`, `render.py:191`,
`contacts.py:52`, `contacts.py:86`, `stores.py:19`, `stores.py:77`,
`trips.py:24`, `trips.py:117`.

Every one is `group_id: int | None = None`, and every one silently degrades to
"all households" when the argument is omitted. This is the mechanism by which
all six known bugs were written, and it is still the default at every call site
a future author will reach for — including through the `grocery` façade, which
re-exports the whole set with the same defaults.

**Not itself a leak**; it is the reason the leaks keep recurring. If one thing
here gets changed structurally rather than one-query-at-a-time, I would make
`group_id` a required positional on these helpers, so omitting it is an error at
the call site instead of a silent full-table read. That converts this entire bug
class from runtime to import time.

## S11 — Events orphaned by the migration are invisible forever

**`db.py:189-198`**. The backfill assigns `group_id` only where a store name is
unambiguous:

```sql
UPDATE events SET group_id = (SELECT MIN(s.group_id) FROM stores s WHERE s.name = events.store)
WHERE group_id IS NULL AND (SELECT COUNT(*) FROM stores s WHERE s.name = events.store) = 1
```

The comment says these rows "are visible to nobody", which is the right call for
a colliding name. But `record_event` (`db.py:306`) also has `group_id: int | None
= None`, so any *future* caller that forgets it writes a permanently invisible
history row rather than failing. The append-only log is the one structure where a
silent write-only row is hard to notice. Same remedy as S10.

**Not a leak — a durability gap.** No orphans on a single-household database.

## S12 — `product_sections` and `people`: is global right?

Both are deliberately global. I think **the answer differs for the two**, and
they should not be defended with the same sentence.

**`product_sections` (`catalog.py:12-50`) — global is right. Keep it.**
It maps a normalized product name to an aisle. It contains no personal data, no
quantities, no history — "leite is dairy" is a fact about Portuguese, not about a
household. Sharing it is the feature: a new household inherits a working
vocabulary instead of starting blank, which is what makes `due` and the grouped
list useful on day one. Two caveats worth knowing, neither worth restructuring
for: `section list` (`cli.py:382-384`) discloses *which products someone,
somewhere, has classified* — a weak signal that a household buys caviar; and
`remember_section` lets one household's correction change another's aisle order.
Both are cosmetic. **Do not "fix" this into a per-group table** — it would
silently break cross-language dedup's usefulness and gain nothing.

**`people` (`people.py`) — the table should stay global; the access should not.**
Keyed by actor, which is a person, not a household — and per S8 a person may
belong to two. Splitting it per group would mean Alice's language preference
depends on which list she is talking about, which is wrong. So the *shape* is
right. But two things follow from that, and neither is currently true:
`display_name` and `actor` are exactly the identifiers `94272dda` decided were
too sensitive to print in a list footer, so dumping the table to any caller (S5)
contradicts a decision already made; and a global keyspace with no caller check
means anyone can overwrite anyone's row (S4). **The fix is a gate on `who`, not
a `group_id` on `people`.**

---

## Checked and correct

A clean bill on these — I traced each and could not construct a cross-household
scenario.

| Query | Why it holds |
|---|---|
| `cli.py:477-479` `events` | Always appends `group_id = ?`; `clauses` is never empty. |
| `cli.py:489-500` `history` | `store_row(group_id=caller_group)`, then `trips`/`trip_items` by `store_id`. |
| `cli.py:504-506` `stores` | Explicit `WHERE group_id = ?`. |
| `cli.py:440-448` `layout` | `store_row` scoped both before and after the update; writes by `store.id`. |
| `cli.py:376-380` `section unknown` | `resolve_store` + `current_items`, both passed `caller_group`. |
| `cli.py:386-396` `due` / `stats` | `caller_group` passed through to `insights`. |
| `insights.py:41-55` `purchase_history` | Aggregates `GROUP BY normalized_name` **inside** the `group_id` filter — the aggregate cannot span households when the argument is supplied, and `cli.py` always supplies it. |
| `insights.py:80-90` `on_list` | Joins `items → stores` and filters `s.group_id`. |
| `items.py:60,133,207,263` | All reach the table through `store_row(group_id=...)`; every subsequent query is by `store_id`. |
| `items.py:162-173` `resolve_item` | Keyed on `store["id"]`, and the store row was already scoped. |
| `trips.py:34,123,130,138` | By `store_id` / `trip_id`; `reopen_trip` re-checks `id = ? AND store_id = ?`. |
| `trips.py:96-100` | `DELETE`/`UPDATE ... WHERE store_id = ?`. |
| `stores.py:40-50` | The `events` fallback wraps `EXISTS (SELECT 1 FROM stores WHERE group_id = ?)` — keyed on the name but **scoped by group**, which is correct. |
| `stores.py:83-99` `store_row` | `normalized_name = ? AND group_id = ?`, on both the read and the post-insert re-read. |
| `groups.py:71-86` `members` | Filtered by `m.group_id`. (Ungated at the CLI — that is S9, not a query defect.) |
| `guide.py:141-150` `store_names` | `WHERE group_id = ?`, and returns `[]` on `None` rather than falling back. |
| `db.py:191-196` | Keyed on the store name, but only to *assign* a group; ambiguous names deliberately left `NULL`. Correct. |
| `render.py:191-214` `last_touched` | Fixed in `94272dda`; verified below. |
| `contacts.py:86-99` `mark_delivered` | Fixed in `94272dda`; scopes via `shares → stores`. |

## Regression check — the six known instances

Re-run against `f92fcc45` on a fresh two-household database. All six hold:

| # | Site | Fixed in | Re-verified |
|---|---|---|---|
| 1 | `cli.py` `events` by store name | `23b8136a` | scoped |
| 2 | `cli.py` `stores` listing all groups | earlier | scoped |
| 3 | `insights.py` `due` / `purchase_history` | earlier | scoped |
| 4 | `contacts.py:55` `prepare_share` | `94272dda` | B's share renders `- Beans`, not A's `Caviar` |
| 5 | `render.py:191` `last_touched` | `94272dda` / `ae0002aa` | B's footer reads `by Bob`, not A's number |
| 6 | `trips.py:45` close guard | `94272dda` | A's retry suppressed despite B's activity |

## One non-scoping bug, noted in passing

**`items.py:219-228`** — the "bought something that was not on the list" insert
omits `canonical_name`, which is `NOT NULL DEFAULT ''`:

```
[('Caviar', 'caviar'), ('Leite', '')]
```

The row is created with an empty canonical key, so cross-language dedup misses
it: buying `leite` this way and later adding `milk` produces two rows instead of
merging. Every other insert path sets it. Not a scoping issue — flagged because
I was in the file and it is a one-line fix.

---

## If I were prioritising

1. **S1** — decide whether `group`/`members` are operator-only, write that
   boundary down, and confirm the plugin cannot reach them. Everything else on
   this page is reachable through S1 anyway.
2. **S2 + S3** — give `contacts` a `group_id`. The share-redirect is the only
   finding here that moves data to a party of the attacker's choosing.
3. **S10** — make `group_id` required on the scoped helpers. This is what stops
   a seventh instance; the rest of the list is the first six's siblings.
4. S4, S5, S9 — gate `who` and `group` reads.
5. S7/S6 — either scope `webtest.py` or state in the file that it is
   single-household. Right now it is half-scoped, which is the worst of both.

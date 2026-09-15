# Roadmap

Scoped ideas, one at a time. Each entry states where the work lands, what is
genuinely unknown, and what "done" means. Nothing here is committed to until it
moves to In progress.

The app is two layers, and most ideas split across both:

- **Agent layer** — `skills/grocery-list/SKILL.md`. Interpretation, when to ask
  versus assume, phrasing, language. Managed outside this repo by ClawHub.
- **Engine layer** — `grocery.py`. State, dedup, authorization, output rendering.

Guiding constraint: this is used **on the move, on unstable service**. Every
question the agent asks costs a round trip that may not arrive. Prefer a
reversible assumption plus a short statement of what was assumed.

---

## 1. Fewer follow-up questions

**Layer:** agent + engine. **Size:** small-medium. **Blocked by:** nothing.

Today the agent asks whenever the store is ambiguous (`SKILL.md`, Capture step
1), and `buy`/`remove` now refuse an ambiguous item name outright. Both are
correct at a desk and wrong in a parking lot.

Cases to map, each with an assumption instead of a question:

- **No store named.** Fall back to the last store touched, else a configured
  default. Say which was used.
- **Ambiguous item.** If exactly one match is still `needed`, take it. Fall back
  to most recently updated. Report the choice; `unbuy` makes it cheap to undo.
  Keep the hard error only for a true tie.
- **Item not on the list.** For `buy`, add it as already purchased rather than
  erroring — the person is standing in the store holding it.
- **Unparseable quantity.** Store quantity 1 and keep the raw text in `note`.

Unstable service also means **repeated and out-of-order commands**:

- `add` and `buy` are already idempotent. `close` is not — a retried "close the
  trip" opens a second trip and rolls items twice.
- Needs an idempotency guard on `close` and a `reopen` to undo one.

**Done when:** the common paths run without a clarifying question, every
assumption is stated in the reply, and a double-`close` is harmless.

## 2. Groups, members, and user recognition

**Layer:** engine (schema), agent (attribution). **Size:** large.
**Blocked by:** how OpenClaw exposes the inbound sender to a skill.

Household groups whose members may read and contribute to a list, identified by
WhatsApp number.

Planned deployment: a **second WhatsApp account dedicated to these projects**,
run from OpenClaw and open to allowlisted users. That settles most of the design.

Findings so far:

- The WhatsApp channel is provisioned in OpenClaw (`credentials/whatsapp-*.json`)
  but `allowFrom` is empty and no pairing is requested. The identity primitive
  should arrive free from the gateway; nobody is authorized yet.
- With a dedicated bot number, the sender's number is a stable identity, and the
  two allowlists get distinct jobs rather than duplicating each other: the
  gateway's `allowFrom` decides **who may talk to the bot at all**, and group
  membership decides **which lists they may touch**. The existing `contacts`
  table should be folded into the same registry, keyed by phone number, so
  sharing and contributing resolve to one identity.
- Lists are currently scoped by store alone. Group scoping means a migration of
  `items`, `trips`, and `shares`, plus an `added_by` attribution column.

**Context isolation is a hard requirement here, not a nicety.** A second account
serving other people is a shared context in the sense `AGENTS.md` already
defines: `MEMORY.md` and personal Google data must never load in those sessions.
A household member texting the grocery bot must be able to add milk and nothing
else — not read the owner's mail, calendar, or memory. Whatever runs that account
needs a restricted profile, and that boundary should be tested deliberately.

**Open question to settle before building:** does a skill invocation see the
sender's number, or only the message text? Everything else depends on it.

**Done when:** a named group has members, a member's message is attributed to
them, and a non-member is refused.

## 3. Portuguese and English

**Layer:** agent, plus one engine question. **Size:** small, split in two.

Two separable halves, and only the second is real work:

- **Replying in the user's language** already works; the model does it. Worth
  writing down in `SKILL.md` as a rule (answer in the language asked, keep
  stored item names as the person wrote them) rather than building anything.
- **Cross-language dedup is a genuine gap.** `normalized()` casefolds but does
  not know `leite` and `milk` are one item, so a bilingual household silently
  gets two rows. Fixing it needs a synonym table mapping variants to one key.

The dedup half only bites once several people share a list, so it pairs
naturally with idea 2. Section labels (idea 5) need to be bilingual too.

**Done when:** `leite` and `milk` merge into one row on the same list, and
replies come back in the language of the request.

## 4. Output formats

**Layer:** engine (rendering), agent (delivery). **Size:** small.
**Blocked by:** idea 5 for the grouping.

The full list is the case that matters. `render_list` exists but is only reachable
through `share`; `list` emits raw JSON. Rendering should live in one place with a
`--format text|json` switch.

Constraints from `AGENTS.md`: on WhatsApp, no markdown tables and no headers —
bullets and bold or caps only.

Wanted: grouped by section, quantity and unit inline, needed separated from
purchased, a count, and short confirmations for `add`/`buy` that do not replay
the whole list.

**Done when:** "show me the full list" returns one scannable WhatsApp-safe
message and the CLI can emit the same text.

## 5. Product to section categorization

**Layer:** engine. **Size:** medium. **Blocked by:** nothing.

Map each item to an aisle or section so the list can be walked in store order.

Existing-solutions preflight: the available open datasets are ML-scale and built
for training classifiers over retail catalogs — FooDI-ML (2.8M images, 33
languages), a Brazilian retail set (153k items), Midiadia's Spanish taxonomy.
None ships as a drop-in bilingual keyword-to-aisle map, and all are far oversized
for a household list of a few hundred distinct items. Recommendation: a small
curated bilingual map in this repo, with the model classifying anything unknown
and the result persisted, so the household's own vocabulary accumulates. Cheap,
offline, no dependency.

Schema note: key the section off a `product_sections` lookup on the normalized
name rather than a column on `items`, so a learned mapping survives the item
being bought and removed.

Per-store aisle ordering is a later refinement, not part of this.

**Done when:** items carry a section, unknown products get classified once and
remembered, and the labels work in both languages.

## 6. Event log, aggregates, and reminders

**Layer:** engine. **Size:** medium, split in two. **Blocked by:** 6b needs idea 5.

A durable log of item, status, date, and actor, cheap to query, so the agent can
answer "am I missing something from produce?" instead of only reciting the list.

**The substrate does not exist yet, and the docs overstate what is kept.**
`item_sources.item_id` is `ON DELETE CASCADE`, so deleting an item deletes its
history: closing a trip drops the provenance of everything purchased, and
`remove` erases the fact that an item was ever wanted. Measured on a scratch
database, two ingested items leave two source rows, one after a close, zero
after a remove. `trip_items` survives, so purchase outcomes persist, but the
source, timing, and eventual actor do not.

**Timing argument: do the log early.** The live database is still empty, so
getting the event model right now costs nothing. Every week of real use turns it
into a lossy backfill from `trip_items`, which never held an actor column.

- **6a — the log.** An append-only `events` table that no cascade can reach:
  item name, normalized key, store, action (added, purchased, unpurchased,
  removed, rolled over), quantity, unit, source, timestamp, and a nullable
  `actor`. Nullable so idea 2 fills it in later without a second migration.
  Also fixes the audit guarantee the quick guide already claims.
- **6b — aggregates and reminders.** Computed on read with plain SQL, not
  materialized: a household of a few thousand events never needs a cache, and a
  stale aggregate is worse than a slow one. Wanted: last purchased, purchase
  frequency, typical interval, and "usually bought by now but not on the list",
  filtered by section. The section filter is why this waits on idea 5.

**Done when:** history survives a close and a remove, and the agent can answer
what is missing from a named section using the household's own past.

## 7. Deterministic paths and model routing

**Layer:** engine + agent config. **Size:** small as an added criterion, larger
as routing. **Blocked by:** 7a is mostly folded into ideas 1, 3, 4, 5, 6.

`grocery.py` calls no model at all — it is stdlib only, with no SDK anywhere in
the project. Every model trip comes from the agent that drives it, so the way to
cut trips is to move work out of the agent and into the CLI.

**7a — the principle: the model does perception, code does everything else.**
Turning messy text, voice, or an image into structured items needs a model.
Nothing after that step does. Audit of what is model-mediated today and what it
should become:

| Path | Today | Deterministic form | Covered by |
|---|---|---|---|
| Which store | agent asks | last used, then default | idea 1 |
| Which item, when ambiguous | agent picks or CLI errors | ranked resolution | idea 1 |
| Rendering the full list | agent composes prose each time | `list --format text`, sent verbatim | idea 4 |
| Product to section | model classifies every time | classify once, then table lookup | idea 5 |
| `leite` = `milk` | model reasons about it | alias table | idea 3 |
| "bought this recently?" | model summarizes history | SQL over the event log | idea 6b |
| "2 dozen eggs" | model NLU | regex for the common shape, model for the rest | **new** |

Most rows are already on this roadmap. So 7a is mainly a **done-criterion to add
to those items** — the happy path must cost zero model trips — plus one genuinely
new piece, a deterministic quantity and unit parser for the common
`<number> <unit> <name>` shape in both languages.

**7b — routing what remains. Deferred: no local models for now.** Everything
that needs a model goes to the primary, `openai/gpt-5.6-sol`.

The consequence matters more than the routing would have. With no local tier,
**every remaining model trip is a network round trip**, on the same unstable
service idea 1 is built around. That turns 7a from a cost optimization into a
reliability requirement: a path that needs a model is a path that can fail in
the aisle. Deterministic paths keep working when the network does not.

The fleet as configured, kept here in case this is revisited: local llama.cpp
offers `local-qwen`, `local-br` (Portuguese-tuned) and `local-gemma`, all
text-only at 16k context, so image capture could never have run locally anyway;
`llama-server` keeps one model resident (`--models-max 1`) and idle-stops after
five minutes, so a shopper would have paid a cold-load penalty. Anthropic models
are also configured through the `claude-cli` runtime and unused.

**Done when:** the common paths make no model call, so that losing service
degrades what the agent can interpret, not what the list can do.

---

## Suggested order

1. **Idea 6a** — the event log. Free to get right while the database is empty,
   a lossy backfill once it is not, and the substrate the rest reads from.
2. **Idea 1** — no dependencies, pays off on every trip, and closes the
   double-`close` hole. Costs the same whenever it is done.
3. **Idea 5, then 4** — categorization exists to make the full list scannable;
   doing 4 first means rendering it twice.
4. **Idea 6b** — aggregates and reminders, once sections exist to filter by.
5. **Idea 3** — the language rule now, the dedup half alongside idea 2.
6. **Idea 2** — largest blast radius, and it needs the sender-identity answer.

**Idea 7a is not a step in this sequence.** It is a criterion applied to each
of the others as it is built: if the happy path still needs a model trip, the
item is not done. Idea 7b routing comes last, once there is something left to
route.

---
name: "grocery-list"
description: "Manage multimodal store lists, trip rollover, and allowlisted sharing over WhatsApp."
---

# Grocery list

Use for adding, cleaning, viewing, purchasing, closing, or explicitly sharing grocery lists.

Canonical local utility:

```text
/home/openclaw/.openclaw/workspace/tools/grocery-list/core/grocery.py
```

Quick guide:

```text
/home/openclaw/.openclaw/workspace/tools/grocery-list/core/README.md
```

## Assume, act, state

This is used on the move, on unstable service, over WhatsApp. Every question you
ask costs a round trip that may never arrive, and a shopper standing in an aisle
cannot answer it.

**The rule: assume where a mistake is cheap to reverse, ask where it is not.**

- Reversible — `add`, `ingest`, `buy`, `unbuy`, `list`, `events`, `history`,
  and `close`. Take the most likely reading, run the command, and state the
  assumption in one short clause. Never ask first. `buy` is undone by `unbuy`,
  `close` by `reopen`, an unwanted `add` by `remove`.
- Irreversible — `remove` alone among the item operations. It deletes the row
  and nothing restores it. Ask.
- Leaves the machine — `share` and `allow add`. Ask, for a different reason:
  reversibility is not the point once the data is in someone else's hands.

State an assumption inline, never as a separate message: “Added to Costco (last
store you used).” One clause, not a paragraph. Do not apologize for assuming and
do not offer alternatives unless asked.

If an assumption was wrong the user says so and you fix it — that is one round
trip instead of two.

### What to assume

- **No store named.** Omit `--store` and let the CLI resolve it: last store
  touched, then the only store on file, then `$GROCERY_DEFAULT_STORE`, then an
  honest error. It reports the choice in `assumptions`, e.g. `["used Costco, the
  last store touched"]`. Relay that. Do not run `events` or `stores` to work the
  store out yourself, and do not ask — only the error at the end of that chain
  is worth a question, and it says exactly what is missing.
- **Ambiguous item name.** Items are unique per store, name, and unit, so `buy
  milk` can match `Milk (gal)` and `Milk (L)`. For `buy` and `unbuy` the CLI
  already breaks the tie — still `needed` first, then most recently updated —
  and reports it in the `assumptions` array of the JSON. Relay that line; do not
  re-derive it and do not pass `--unit` unless the user named a unit.
- **`buy` on an item not on the list.** The person is standing in the store
  holding it. The CLI adds it as already purchased and says so in `assumptions`.
  Do not `add` first, and never bounce an error back.
- **Unparseable quantity.** Store `quantity: 1`. The person's own words go only
  in `--raw-text`; `note` holds at most a short normalized hint (`~2`,
  `pequeno`, `a de coco`), never their sentence. Do not ask what “a couple of”
  means.
- **Unknown store name on `add`.** `add` and `ingest` create the store. Just
  add, and say the store was new.
- **“Close the trip”, possibly twice.** Just call `close`. A retry on bad
  service does not mint a second trip: a close following another close closely,
  with no item activity in between, returns the original trip with
  `"duplicate": true`. When that flag is true, say the trip was already closed —
  do not report it as new and do not recite the same purchases again.

Every mutating command returns an `assumptions` array — `add`, `ingest`,
`list`, `buy`, `unbuy`, `remove`, `close`, `reopen`. It is empty when nothing
was assumed. When it is not, one short clause of it goes into your reply. That
array is the whole point of not asking; dropping it turns a stated assumption
into a silent one.

### What to still ask

- `remove` — confirm the item before deleting; it is the one item operation
  with no way back. `remove` does not guess between units either: an ambiguous
  name is an error, and the right move is to show the user the units and let
  them pick, not to choose one. If the user actually meant “I bought it”, use
  `buy` — reversible, and it keeps the purchase in the history.
- `share` and `allow add` — always. See the sharing section.

Nothing else is worth a question. If you catch yourself drafting one about a
store, a unit, or a quantity, the answer is a command plus a clause.

## Capture

1. Identify the store. If none is stated, omit `--store` and relay the
   assumption the CLI returns; do not ask.
2. Extract only visible or spoken grocery items.
   - Text: retain meaningful brand, size, quantity, and preference details.
   - Voice: transcribe, then extract.
   - Image: inspect the image; do not invent obscured products.
   - Video: combine the audio transcript with representative visible frames,
     then deduplicate.
3. Normalize into a JSON list of objects with `name`, optional `quantity`,
   `unit`, and `note` (a short hint, never the person's words). Keep names in
   the language the person used.
4. Record the source:
   ```bash
   python3 /home/openclaw/.openclaw/workspace/tools/grocery-list/core/grocery.py ingest \
     --store STORE \
     --source-type text|voice|image|video \
     --source-ref SOURCE \
     --raw-text TRANSCRIPT \
     --items-json JSON \
     --actor WHO
   ```
   Plain typed items can go through `add` instead:
   ```bash
   python3 /home/openclaw/.openclaw/workspace/tools/grocery-list/core/grocery.py add \
     --store STORE --actor WHO "milk" "eggs"
   ```
5. Report briefly what was added versus merged. Flag uncertain recognition
   instead of storing a guess — an item you could not read is a question worth
   asking, because storing a wrong guess is not free to reverse.

## Shop

- Show:
  ```bash
  python3 /home/openclaw/.openclaw/workspace/tools/grocery-list/core/grocery.py list --store STORE
  ```
  Add `--needed-only` for “what is still needed”. Output is JSON; you render it.
- Mark purchased:
  ```bash
  python3 /home/openclaw/.openclaw/workspace/tools/grocery-list/core/grocery.py buy \
    --store STORE --actor WHO ITEM...
  ```
- Undo:
  ```bash
  python3 /home/openclaw/.openclaw/workspace/tools/grocery-list/core/grocery.py unbuy \
    --store STORE --actor WHO ITEM...
  ```
- Delete permanently, recording no purchase (ask first):
  ```bash
  python3 /home/openclaw/.openclaw/workspace/tools/grocery-list/core/grocery.py remove \
    --store STORE --actor WHO ITEM...
  ```
- Close:
  ```bash
  python3 /home/openclaw/.openclaw/workspace/tools/grocery-list/core/grocery.py close \
    --store STORE --actor WHO
  ```
- Undo a close:
  ```bash
  python3 /home/openclaw/.openclaw/workspace/tools/grocery-list/core/grocery.py reopen \
    --store STORE --trip-id N --actor WHO
  ```

Closing archives a dated snapshot, removes purchased items from the live list,
and leaves missing items needed. It returns `duplicate: true` instead of opening
a second trip when it is a retry — read the flag before you word the reply.

`reopen` puts the archived trip back: every item returns to the live list with
the outcome it was closed with, purchased still purchased, and the trip is
deleted. `--trip-id` defaults to the most recent trip for the store. This is the
recovery path for a close the user did not want, so offer it rather than
rebuilding a list by hand — and say it exists when someone hesitates about
closing.

`--store` is optional on every command that takes it (`add`, `ingest`, `list`,
`buy`, `unbuy`, `remove`, `close`, `reopen`, `history`, `share`). Pass it when
the user named a store; omit it otherwise and relay the assumption.

`buy`, `unbuy`, and `remove` take `--unit` to pick among items that share a name
but differ by unit: `--unit gal`, or `--unit ""` for the unitless row. On `buy`
and `unbuy` pass it only when the user named a unit — otherwise let the CLI
assume and report. On `remove` it is how the user's answer gets applied.

`add`, `ingest`, `buy`, `unbuy`, `remove`, `close`, and `reopen` take
`--actor`. Pass the
sender's identity when you know it; it is recorded in the event log. Omit it
rather than guessing — the field is nullable on purpose.

## History

```bash
python3 /home/openclaw/.openclaw/workspace/tools/grocery-list/core/grocery.py events \
  --store STORE --item ITEM --action ACTION --actor WHO --limit N
```

All filters are optional. `action` is one of `added`, `merged`, `purchased`,
`unpurchased`, `removed`, `trip_purchased`, `trip_missing`, `reopened`. This log
survives `remove`, `close`, and `reopen`; use it for “when did I last buy
coffee?” and “who added this?”. `history` shows archived trips, most recent
first, with the trip ids `reopen` takes.

## Language

- **Pick the reply language in this order: an explicit request, then the
  person's stored preference (`who set <actor> --lang pt`), then the default.
  Never the language of the items.**
- Pass `--actor` on every command, including `list`, so the CLI can resolve
  that preference itself. A message that is *just a list* — `leite, arroz,
  pão` — carries no prose to read a language from, and the items are not a
  signal: the same words could come from someone who wants English back. This
  is the case the preference exists for.
  A list full of `leite`, `arroz`, and `pão` answered in English stays in
  English; the items are data, not a language signal. Pass the choice to the
  CLI as `--lang en` or `--lang pt` and it renders every word it owns —
  headers, section labels, confirmations, assumption clauses, timestamps — in
  that language, with no composition needed from you.
- Portuguese (pt-BR) and English are both first-class; match the person, not a
  default.
- Store item names exactly as the person wrote them. Never translate an item
  into English for storage. “leite” is stored as `Leite`.
- Translate only your own words. The CLI already translates everything it
  renders, so relay its output rather than restating it.
- Confirmations name a single item and count several — `Adicionados 3 itens em
  Costco.` Do not expand that back into a list of names; the person just sent
  them. If they ask what is on the list, run `list`.
- **Known gap, do not paper over it:** the engine casefolds but has no synonym
  table, so `leite` and `milk` become two rows on the same list. If you notice
  the duplicate, say so plainly and offer to `remove` one; do not claim they
  were merged. Cross-language dedup is roadmap item 3.

## Replies

WhatsApp rules from `AGENTS.md`: no markdown tables, no headers. Bullets, and
**bold** or CAPS for emphasis.

Full list — one scannable message, needed first, quantity and unit inline,
purchased collapsed to a count and names:

```text
*COSTCO* — 5 needed
• Milk × 2 gal
• Eggs × 12 — organic
• Coffee
Bought (2): Bread, Butter
```

Confirmations after `add`, `buy`, `unbuy`, `close`, `reopen` — one line. Never
replay the list:

- “Added milk and 2 dozen eggs to Costco (last store you used).”
- “Adicionei leite e 2 dúzias de ovos no Costco.”
- “Marked Milk (gal) bought — 4 left.”
- “Costco trip closed: 8 bought, 2 carried over. Say reopen to undo.”
- `duplicate: true` → “That trip was already closed.” Nothing more.

A trailing count is useful; the full list is not. Send it only when asked.

## Allowlist and external sharing

Adding or changing a contact requires explicit user authorization and an exact
alias, channel, and destination:

```bash
python3 /home/openclaw/.openclaw/workspace/tools/grocery-list/core/grocery.py allow add ALIAS \
  --channel CHANNEL --target TARGET
```

Never send merely because a list was displayed or rendered.

For an explicit request such as “share the Costco list with Mom”:

1. Prepare the share:
   ```bash
   python3 /home/openclaw/.openclaw/workspace/tools/grocery-list/core/grocery.py share \
     --store STORE --contact ALIAS
   ```
2. If the contact is rejected, stop. Do not bypass the allowlist.
3. Verify the returned channel and target match the intended recipient.
4. Send the returned payload using OpenClaw messaging.
5. Only after confirmed delivery:
   ```bash
   python3 /home/openclaw/.openclaw/workspace/tools/grocery-list/core/grocery.py delivered SHARE_ID
   ```

A prepared share is not proof of delivery.

## Boundary

- The SQLite database is local: `tools/grocery-list/core/data/grocery.sqlite3`. Nothing
  leaves the machine except an allowlisted share you deliver yourself.
- OurGroceries sync is **out of scope by decision, not pending work.** There is
  no public API and no OAuth flow; every available integration is unofficial,
  reverse-engineered from the web app, and authenticated with the account
  password. Do not offer it, and do not describe it as blocked on an
  authorization the user could grant. Sharing stays a rendered payload delivered
  over an allowlisted channel.
- `item_sources` rows are deleted with their item on `close` and `remove`. Read
  history from `events`, never from `item_sources`.

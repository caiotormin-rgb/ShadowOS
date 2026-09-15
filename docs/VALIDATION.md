# Export validation

A review of export commit `340f2f5` on September 15, 2026 ran the following
Python and Node suites without live account credentials:

| Suite | Cases run | Skipped |
|---|---:|---:|
| Six context layers | 719 | 11 |
| Grocery core | 402 | 1 |
| Doctor core | 33 | 0 |
| Household router | 17 | 0 |
| Household configuration (`test_prepare.py`) | 8 | 0 |
| Calendar ICS guardrail | 7 | 0 |
| **Total** | **1,186** | **12** |

There were 1,174 passes and no failures. The review did not rerun the
TypeScript plugin suites, production dispatch tests, media benchmarks or
live end-to-end workflows. Older validation counts in the deployment
handoffs refer to their stated versions and environments.

Passing suites did not settle correctness. Separate synthetic reproductions
found that an older calendar invitation could overwrite a newer event and
that concurrent doctor send calls could send an approved draft twice. Those
implementation issues remain open in this documentation revision.

## Reproduce the dependency-free suites

From the repository root, with Python 3.12 and Node 24 available:

```bash
for layer in mail-context calendar-context drive-context; do
  (cd "layers/openclaw/$layer" && PYTHONPATH=../mail-context python3 -m unittest discover -s tests -t .)
done
for layer in ledger life-index mail-enrichment; do
  (cd "layers/openclaw/$layer" && PYTHONPATH=../mail-context python3 -m unittest discover -s tests)
done
PYTHONPATH=tools/grocery-list/core python3 -m unittest discover -s tools/grocery-list/core/tests
(cd tools/doctor-search/core && PYTHONPATH=. python3 -m unittest discover -s tests)
node --test tools/household-router/test/*.test.mjs
python3 tools/household-config/test_prepare.py
python3 tools/automations/calendar-ics-guardrail/test_calendar_ics_guardrail.py
```

These commands reproduce the selected suites, not the private corpus
measurements. Optional integration dependencies account for skipped cases.
The export's [plugin setup limitations](../tools/README.md) are separate.

## Portfolio example

The multimodal examples in the README and chat guide use synthetic products,
media captions and interpreted requests. Separate temporary SQLite runs used two fictional
members sharing a household, with Costco as the default store and either
Portuguese or English as the preferred language. Direct calls to
`agent_api.handle` verified the following:

1. Three products from the described fridge video are added. The yogurt's
   fallback is stored as an item note; no second yogurt item is added.
2. Marking the milk bought changes that existing item and leaves two needed.
3. Reversing that purchase restores the milk to needed and preserves the
   other items, with the store resolved from the saved preference.
4. Two screenshot-derived items are added, and the other member can read
   the same five-item list with the fallback note intact.

Dialogue and media descriptions are authored reconstructions. Replies use
verified item facts, with the fallback note made explicit for readability.
No real audio, image or video recognition, model interpretation, pronoun
resolution, substitution execution or WhatsApp delivery was tested by this
walkthrough. It validates the resulting list operations, not an end-to-end
multimodal interaction. Historical media tests and conversation scenarios
remain inspectable in the exported source.

### Additional chat examples

[Chat examples](CHAT-EXAMPLES.md) expands the authored scenarios to shared
shopping, ambiguous purchases, history, recipe capture, and Doctor intake,
selection and outreach. These are illustrations of the intended conversation,
not recorded model sessions or evidence of a full Doctor launch.

For this documentation update, two existing grocery tests were rerun for
partial-success clarification and member-visible purchase history. Five
existing Doctor tests were rerun for email verification, draft guards,
approval before sending, changed-draft rejection and replacement of an old
approval. **All seven passed**, using temporary stores and a fake mailer.
These are a subset of the earlier suite, not additional unique test cases.
The walkthrough passed the list-operation checks above with Portuguese and
English product names. The current presentation mixes the two languages
across scenarios; dialogue wording remains authored.

No real provider search, recipe fetch, email delivery or appointment booking
was performed. The Doctor dialogue follows the exported conversation
instructions and outreach template; testing the underlying guards does not
establish that a live model always follows those instructions.


### Visual presentation

The banner, grocery workflow and three conversation SVGs are authored
illustrations. Grocery and Doctor cards follow the documented workflows;
Doctor remains in early testing. Personal-record dates, amounts, businesses
and source titles are fictional. The support-email segment is a proposed
composed workflow, not an implemented life-index send capability.

The earlier grocery example validates interpreted list operations. The new
personal-record dialogues and tool-call examples were checked against tool
schemas and access boundaries, but were not run against a personal catalog.
No photo recognition, support drafting or email delivery was exercised.

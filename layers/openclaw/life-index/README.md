# life-index

An encrypted, local catalog of the documents that matter — retrievable by
Caio and (later) by the OpenClaw agent. Design:
[`docs/plans/life-index-design.md`](../../../docs/plans/life-index-design.md).

Stdlib only. The one optional dependency (Docling, for scans) is invoked
out-of-process, so this package stays installable by copying a directory.

## Setup — once

Two modes. Pick deliberately; the CLI refuses to guess.

```bash
bin/li-plaintext   # UNENCRYPTED store. Requires typing 'plaintext' to confirm.
# or
bin/li-init        # encrypted; prompts for a passphrase, twice. NOT recoverable.
bin/li-mount       # prompts again; mounts the plaintext view
```

`bin/li-encrypt` migrates an existing plaintext store into an encrypted one
later by moving files — nothing is re-extracted or re-cataloged.

The passphrase is asked for at every mount, i.e. after each reboot. gocryptfs
cannot reset it and neither can anyone else — put it in a password manager
before running `li-init`.

## Daily use

```bash
cp ~/Downloads/drivers-license.pdf ~/life-index/plain/consume/
python3 -m lifeindex.cli consume          # catalog everything dropped in
python3 -m lifeindex.cli search escritura Rua Example
python3 -m lifeindex.cli get <sha256>
python3 -m lifeindex.cli status
bin/li-umount                             # documents encrypted at rest again
```

`consume` also takes a path and a `--source`, which is how the Gmail harvest
feeds the same catalog:

```bash
python3 -m lifeindex.cli consume /tmp/mailpilot --source gmail
```

## Importing a Gmail harvest

```bash
python3 -m lifeindex.cli consume /tmp/mailpilot --source gmail --harvest
```

`--harvest` understands the fetcher's layout (one dir per message with
`meta.json`, `body.txt`, attachments). Without it the directory is walked as a
flat pile of files, which files `meta.json` as a tier-1 contract because it
contains the subject line. Attachments become documents; bodies become tier-3
correspondence; `body.html` is skipped.

The importer also records **provenance**: the sender becomes a `correspondents`
row, and each artifact gets `sender:`, `domain:` and `year:` tags plus a
`doc_date` from the mail header. That is what makes the calibration report
groupable by sender without re-parsing `source_ref`.

## The calibration report

After a bulk harvest, this is the thing to read:

```bash
python3 -m lifeindex.cli calibrate \
    --manifest /tmp/mailharvest/manifest.jsonl \
    --census   /tmp/sender-census.csv
```

It writes `calibration-report.md` and `sender-verdicts.csv` into the store.

**Organised by sender, because that is the only durable key.** Measured on the
64,200-message corpus: 41 senders cover 50% of the non-marketing mail, 154
cover 80%, and only 2% of messages come from a sender seen once, while
subject-derived keys are 82% singletons. So the report ranks senders and asks
for ~154 decisions rather than tens of thousands.

Nine sections: the ranked sender list · the cost curve (how many decisions buy
50/80/95% coverage) · type × tier and year · the biggest documents · the
untyped pile (every row is a classification rule that does not exist yet) ·
extraction failures · fetch-side noise from the manifest (chrome skipped,
messages that yielded nothing — the catalog cannot know these, since chrome is
never cataloged) · a seeded random sample of 30 with excerpts, for eyeballing
precision · and the levers, each with the number of currently-kept artifacts
it would drop.

Edit the `verdict` column of `sender-verdicts.csv` (`never` / `always` /
blank) and feed it back to `mailctx.selectrun --verdicts`. It persists into
`sender_rules` and terminates those senders before any future API call.

Both files contain sender addresses, subjects and document excerpts. They live
in the store. They do not go in git.

## Agent access

`python3 -m lifeindex.mcp` is a read-only MCP stdio server exposing
`artifact_search`, `artifact_get`, `artifact_fields`, `artifact_status`.

Tier 1 and 2 documents return metadata, extracted fields, and a path — not
their text. The server is reached from chat apps, and "what is my CPF" should
not be answerable by anyone who can message the gateway. Override with
`LIFE_INDEX_AGENT_TEXT=all`.

## What it does to a file

hash → dedupe → extract text → classify → extract fields → catalog + blob.

- **Dedupe is by content**, not filename. Verified on real data: three copies
  of a logo collapse to one blob, while a draft and an executed contract stay
  separate because their bytes differ.
- **Extraction** is `pdftotext` first (it handled 5 of 5 pilot documents,
  including a 25-page school document), falling back to Docling only when text comes back
  thin — i.e. for scans. A document that yields nothing is flagged
  `needs_review`, never silently unsearchable.
- **Chrome is skipped**: images at or under 40 KB are logos and signature
  graphics. They were ~80% of pilot attachments by count.
- **Classification is conservative.** An unrecognised document is tier 3 and
  untyped; nothing is guessed into tier 1.
- **Fields** are deterministic patterns only (CPF, CNPJ, RG, BRL/USD amounts,
  matrícula, policy and student numbers, Docusign envelope ids), each
  validated against a real document. Model lanes come later and record their
  own provenance.

## Search

Three widening steps, stopping at the first that hits: exact AND, prefix AND,
prefix OR. Prefix matching is there because FTS5 does not stem — `immunization`
would otherwise miss a document saying `Immunizations` — and prefix beats a
Porter stemmer here because the corpus is bilingual.

Every term is quoted, so `*`, `OR`, and `NOT` in a query are matched as
literal words, never honoured as FTS5 syntax.

## The mount guard

`paths.require_mount()` reads `/proc/self/mountinfo`, not "is the directory
empty". An unmounted store is an ordinary empty directory, and writing into it
would put plaintext tax and identity documents on an unencrypted disk. Every
write path checks it first.

## Tests

```bash
python3 -m unittest discover -s tests
```

51 tests. The `RegressionTest` class holds bugs found by running the pipeline
over the 23 real pilot documents rather than over fixtures: amounts wrapped
across PDF lines, hedged bilingual queries, and `immuniz` as a word-anchored
pattern that matched none of the three real spellings.

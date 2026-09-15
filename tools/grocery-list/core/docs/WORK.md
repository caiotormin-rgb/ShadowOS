# Working agreement

Several Claude sessions share this one checkout. There is no branch isolation,
so the rules below are what stop us overwriting each other.

## Rules

1. **Claim before you edit.** Say which files you are taking, in the table
   below, before the first edit. One writer per file.
2. **Commit only what you authored.** Never `git add -A`, and never stage a
   file you did not write — someone else's half-finished edit ends up in your
   commit. Name paths explicitly.
3. **Run both suites before committing**, not just the tests you touched:
   `python3 -m unittest discover -s grocery-list -p 'test_*.py'` **and**
   `npm test` in `tools/grocery-list/plugin/`. Neither runner executes the other's
   tests, and the operator-only boundary is asserted in both — a green Python
   suite is not evidence about the vitest half. See `TRUST-BOUNDARY.md`.
4. **Commit your claimed file as soon as it is green.** Do not hold finished
   work in the tree while you write a report. A whole-file write destroys a
   concurrent writer's version silently — no conflict, no error, and a green
   suite afterwards, because whatever survives is self-consistent. Once nothing
   was committed to a path, "no collision happened" and "the collision left no
   trace" are indistinguishable afterwards. Committing early makes the tree
   carry the evidence instead of our memories.
5. **Prefer patching to overwriting.** `cat > file` on a path another session
   might hold is the operation with no undo; an exact-string patch fails loudly
   when the text underneath has changed, which is the failure you want.
6. **The working tree is production.** The OpenClaw plugin executes
   `grocery.py` from this directory, not from a commit. A file saved broken is
   a broken WhatsApp bot for everyone on the allowlist. Do not leave the tree
   in a non-running state.
7. **Do not touch, without saying so first:**
   - `data/grocery.sqlite3` — the live list, with real data
   - the OpenClaw gateway, its config, or either allowlist
   - `config/members.json` — who has access
   - `tools/grocery-list/plugin/` — untracked by the owner's choice; rebuild with
     `npm run build` if you change it, since `dist/` is what loads

## Ownership

| Files | Session | Status |
|---|---|---|
| `WORK.md` | grocery-list-21 | this file |
| `contacts.py`, `render.py`, `trips.py`, `cli.py` | grocery-list-f0 | **released**: its three household-scoping leaks landed in `94272dda` (verified 2026-09-13); reclaimed below |
| — | plan-0913 / history | released; `activity` history merged (`42e03799`), DB migrated to user_version 1, deployed 2026-09-13 |
| — | plan-0913 / priv | released; real phone numbers replaced by fictional ones, `test_privacy.py` guard (`687e785a`) |
| — | plan-0913 / audio | released; transcription rewrite merged (`c4d04fe5`), live config switched |
| — | plan-0913 / video | released; ≤15 s video merged (`b299947f`), live config enabled |
| `bench/` | plan-0913 / evalset | branch `worktree-agent-a8b433bd3b8cb2405` (`78a62b41`); on hold until the owner finishes model credentials |
| `openclaw.json`, live deploy | plan-0913 (lead) | only the lead changes live config; see `~/dev_playground/PLAN.md` deploy log |
| — | grocery-list-e2 | released; `AUDIT-scoping.md` landed in `e9b5fb10` |
| — | grocery-list-7d | released; contract tests landed in `ace8adb9`, `4fef1483` |
| — | grocery-list-35 | released; co-reported the same contract work |
| — | plugin-item-shapes | released; merged `643497cd`, built and deployed 2026-09-13 20:04 EDT |
| — | buy-partial-match | released; merged into master and live 2026-09-13 |
| everything else | unclaimed | |

## State

- HEAD `0d9dc4ab`, 182 tests green, whole roadmap shipped.
- Live: gateway running; WhatsApp `tools` allows the owner and Sam Example.
- Tree is clean. Roadmap shipped; scoping audit filed and its one
  member-reachable finding fixed; operator-only boundary asserted in both
  suites.
- **Two sessions independently reported authoring `test_plugin_contract.py`.**
  Only one version survives and it is committed. Whether the other was
  overwritten cannot be determined from the tree, because nothing was committed
  to that path beforehand — which is exactly why rule 4 now exists.
- The same bug class — a query that forgets its household — has now been found
  three times. Assume there are more until an audit says otherwise.

## Decided

- **The household is the owner, Sam Example, Kim and Alex**, all four in one group,
  enrolled across all three allowlists. Do not add or remove anyone without
  the owner saying so. Note that `openclaw channels status` abbreviates the allowlist
  in its text output; read `--json` before concluding a number is missing.

## Open decisions for the owner

- Install `SKILL.draft.md` over the ClawHub-managed skill?
- Track `tools/grocery-list/plugin/` in git, or leave it untracked?

## 2026-09-14 isolated implementation

The codex/shadow-0914 worktree is owned by the Codex team. File claims and current status are maintained in docs/shadow-0914/PLAN.md. The live checkout is not edited during parallel implementation. Old ownership/state entries above are historical.

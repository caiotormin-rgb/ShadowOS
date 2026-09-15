# History

This repository is a filtered export of a private operations repo. The private
repo's history is not published because it contains dated implementation
records that name real properties, prices and people. The commit log below is
the part of that history that is safe to show, and it is included because the
commit discipline is part of the work: most `fix:` subjects state the defect
that was found, not the change that was made.

Fifty commits in the station repo, 2026-08-22 to 2026-09-15, 419 file
changes, about 41k lines added. A second private repo, the agent's own
workspace, holds another 160 commits (2026-08-24 to 2026-09-14) for the
household tools; a selection of its subjects is in
[HOUSEHOLD-TOOLS.md](HOUSEHOLD-TOOLS.md). Every non-trivial change also produced a dated record with the actual
command output as evidence, which is where the private history lives.

```
2026-08-22  Bootstrap torm workspace with OpenClaw layer
2026-08-22  Document OpenClaw's runtime-generated files and workspace collision
2026-08-22  Track station config, Claude memory, and add secret guard
2026-08-22  Install gh CLI to userspace and track its PATH fragment
2026-08-22  Add publish helper that refuses to push if secrets are found
2026-08-22  Record GitHub remote in memory snapshot
2026-08-23  docs(openclaw): stabilize isolated production operations
2026-08-23  fix(security): publish secret scanner
2026-08-23  feat(mail-context): Gmail read-only context layer, authorized and tested
2026-08-23  feat(mail-context): systemd units and operations runbook
2026-08-23  fix(mail-context): a bounded run must not advance the sync cursor
2026-08-23  fix(mail-context): Gmail rate-limits with 403, and completion is not a cursor
2026-08-23  feat(calendar-context): read-only Calendar index, no write path
2026-08-23  feat(drive-context): metadata-only Drive index, content unreachable
2026-08-23  feat(mail-context): MCP server exposing the index to the OpenClaw agent
2026-08-23  fix(record): correct two errors in the MCP wiring handoff
2026-08-23  fix(mail-context): ProtectKernelModules breaks any --user unit
2026-08-23  fix(mail-context): a timer-triggered oneshot must not also Restart=
2026-08-23  fix(record): the unit verification grep matched its own comments
2026-08-23  docs: agent-facing context did not know these layers existed
2026-08-23  docs: comply with the repo's own conventions
2026-08-23  docs: the entry point omitted the two rules that got broken
2026-08-23  feat(record): verify Calendar/Drive API enablement without new scopes
2026-08-23  fix(record): openclaw cannot read anything under /home/caio
2026-08-23  docs(calendar): deployment handoff, with a dependency problem flagged
2026-08-23  feat(auth): manual authorization mode, no callback listener
2026-08-23  fix(record): calendar bounds by window, not count, and authorizes manually
2026-08-24  fix(openclaw): route interactive CLI to production
2026-08-24  feat(openclaw): add explicit cloud and local model lanes
2026-08-24  docs(agents): define Proton Pass machine boundary
2026-08-24  feat(secrets): provision scoped Proton agents on utorm
2026-08-25  feat(mail): life index, entity ledger, and the plan that replaced the old one
2026-08-25  fix(mail-context): rename --exclude to --query; it takes any listing terms
2026-08-25  feat(station): TPM-sealed vault + one-paste lifetime backfill
2026-08-25  fix(station): lifetime backfill needs --full or it silently no-ops
2026-08-25  feat(station): mailctl — five commands for the recurring mail routines
2026-08-25  feat(ledger): MCP server so the agent can answer transaction questions
2026-08-25  docs: portfolio-grade README, publishing boundary, de-identified examples
2026-08-25  docs: package mail + Drive as one product document, and a machine hub
2026-08-25  feat(life-index): catalog Drive documents in place
2026-08-25  fix(pipeline): derive sender classification; share counterparty identity
2026-08-25  fix(life-index): metadata-only artifacts were unfindable; schema never migrated
2026-08-25  fix(pipeline): incremental extraction, and attachment state that cannot lie
2026-08-25  docs(record): verify pointer-artifact search against the live store
2026-08-25  fix(station): the tracked gateway unit was Caio's, and could be deployed
2026-08-25  fix(mail-context): the unit deploys with a plain cp, no sed rewrite
2026-09-15  feat(ledger): scheduled refresh as the index owner, with a shrink guard
2026-09-15  chore(openclaw): remove Caio's rollback bootstrap; docs and scanner follow
2026-09-15  docs(record): life-index MCP handoff, 2026.9.4 upgrade rehearsal, VS Code
2026-09-15  docs(record): schema reconcile findings and verification scripts
```

## How the work was organised

- **Layers.** One directory per installed capability, each with its own README,
  schema, tests and systemd units. A layer can be deployed by copying its
  directory, because nothing in it needs installing.
- **Records.** Every non-trivial change got `records/<date>-<slug>/` with a
  README written last and read first, a `work/` directory holding the actual
  logs and command output, and an `outputs/` directory for anything a human
  had to act on. Records are the evidence trail. They are private.
- **Decision register.** A single master plan carries a numbered table of
  every decision with its status: live, dead, deferred, demoted, superseded.
  Several are marked dead after being built and rejected. One is marked
  "dead, was a bug". If a decision is not in the table, it is not a decision.
- **Handoffs instead of privilege.** The machine has no passwordless sudo.
  When a step needed root, the agent wrote the exact commands into a
  `needs-sudo.md` file with the verification steps, and a human ran them.
- **Assertions instead of remembered steps.** Two read-only check scripts run
  before every commit and fail if the gateway's account boundary or a
  systemd unit's paths have drifted. Both exist because a documented "remember
  to" step was skipped once and a deployed unit sat two releases behind.

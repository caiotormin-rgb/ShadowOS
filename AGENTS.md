# AGENTS.md — torm workspace

Read this first. It describes what is true about this machine as of 2026-08-23.

## The machine

This repo lives on **`torm`**, an Ubuntu 24.04.4 LTS workstation (`caio@torm`,
`<lan-ip>`). torm runs sshd and is an SSH *target*. It has **no outbound SSH
credentials** — if a task says "the remote station", it means someone connects
*to* torm.

**There is no passwordless sudo.** Agents have no interactive terminal, so `sudo`
will fail or hang. Install into userspace (`~/.local`, `~/.openclaw`,
`systemctl --user`). If root is genuinely required, write the command into a
record's `outputs/needs-sudo.md` and hand it to the human — do not attempt it.

## Layers

A *layer* is a capability installed on torm that you can use or must respect.

| Layer | What it is | Context file |
|---|---|---|
| **openclaw** | Self-hosted gateway bridging chat apps (WhatsApp, Telegram, Signal, Discord, Slack…) to AI agents. Runs as a systemd **user** service on loopback. | [`layers/openclaw/AGENTS.md`](layers/openclaw/AGENTS.md) |
| **mail-context** | **Live.** A local SQLite read model of Gmail metadata (64k messages in a 24-month window; lifetime expansion staged), exposed to the OpenClaw agent as four read-only MCP tools. | [`docs/runbooks/mail-context.md`](docs/runbooks/mail-context.md) |
| **calendar-context** | Built and tested, **connected to nothing** — no API enabled, no scope, no token, no data. | [`docs/plans/openclaw-google-calendar-context.md`](docs/plans/openclaw-google-calendar-context.md) |
| **drive-context** | **Retired 2026-08-25** — never connected, and superseded by the Google Drive MCP, which does search, full text and OCR with no credentials of ours. Code kept, plan archived. | [`docs/plans/archive/`](docs/plans/archive/README.md) |
| **life-index** | Document catalog: consume pipeline, extraction, FTS, read-only MCP tools. Store is **plaintext by operator decision**; `bin/li-encrypt` migrates. | [`layers/openclaw/life-index/README.md`](layers/openclaw/life-index/README.md) |
| **ledger** | Transactions keyed on counterparty, extracted from mail. Prototype verified at 84% precision; not yet in production. | [`layers/openclaw/ledger/README.md`](layers/openclaw/ledger/README.md) |
| **mail-enrichment** | Sender classification, entity resolution, bulk harvest, QC benchmark. | [`layers/openclaw/mail-enrichment/README.md`](layers/openclaw/mail-enrichment/README.md) |

**Publishing:** this repo has a GitHub remote and history is permanent.
`records/` and `memory/` are never publishable; `layers/`, `docs/` and
`station/` are. Run `station/scripts/publish-check.sh` alongside
`scan-secrets.sh` — the latter matches credential shapes only and will pass a
file full of home addresses. Details in [`docs/PUBLISHING.md`](docs/PUBLISHING.md).

**Resuming?** Read `.remember/remember.md` first — state, next steps, gotchas.

**The plan for all mail/document work is
[`docs/plans/00-mail-and-documents-MASTER.md`](docs/plans/00-mail-and-documents-MASTER.md).**
It carries a decision register; anything not in it is not a decision. Older
plans are in `docs/plans/archive/` and must not be planned from.

### The Google context layers

Since 2026-08-23 the production agent can read Caio's mail. Three things follow:

- **Gmail is authoritative; the index is a cache.** Deleting
  `/home/openclaw/.local/state/mail-context/` loses nothing but ~30 minutes.
- **Sending is absent by construction, not merely unused.** The grant is
  `gmail.readonly` and the transport allowlists read-only paths. Gmail has no
  draft-without-send scope, so any future drafting phase must widen the grant to
  one that also authorizes sending — the tests in
  `layers/openclaw/mail-context/tests/` are what keep that safe. Do not weaken
  them to make a feature easier.
- **The encryption gate is waived, not passed.** torm's disk is unencrypted, so
  the index is readable by anyone with offline access. The waiver at
  `/home/openclaw/.config/mail-context/encryption-waiver.txt` scopes itself to
  "Gmail metadata, subjects, and snippets" — it does **not** cover the attendee
  addresses calendar-context would store. That decision is open.

### The OpenClaw layer changes your assumptions

Since 2026-08-22 this machine hosts a second agent runtime. That matters to you:

- **You are not the only agent here.** OpenClaw's gateway can run its own agent
  turns with tool access to this filesystem, triggered from chat apps. A file you
  did not write may have been written by it.
- **Port `18789` is taken** by the production Gateway running as the isolated
  `openclaw` Linux account. Do not bind it.
- **`/home/openclaw/.openclaw/` is production live state**, not scratch.
  Editing it by hand can corrupt sessions. Run supported CLI commands through
  `station/scripts/openclaw-prod`. In Caio's interactive shell, plain `openclaw`
  *is* production (a function in `station/bashrc.d/openclaw.sh`) — so
  `openclaw doctor --fix` typed there acts on the live Gateway.
- **Caio's rollback bootstrap (`/home/caio/.openclaw/`) was removed 2026-09-13**
  at his request. A verified archive is at
  `~/archive/2026-09-13-caio-openclaw-rollback.tar.gz` (mode 0600, holds that
  install's credentials). There is no longer a second install to fall back to —
  `records/2026-09-13-remove-caio-openclaw-rollback/`.
- **`~/.npm-global` holds a userspace OpenClaw 2026.9.4** (installed by hand
  2026-09-13, not by an agent). Its default state dir is `~/.openclaw`, so
  running it recreates a fresh install in Caio's home. It is not production.
- **"Workspace" is ambiguous on torm.** `/home/caio/workspace/` is this
  operations repo; `/home/openclaw/.openclaw/workspace/` belongs to the
  production chat agent. Disambiguate before acting on "the workspace".
- **`/home/openclaw/.openclaw/openclaw.json` holds production secrets.** Never
  print it, copy it into this repo, or move credentials through Caio's home.
- **Restarting the gateway drops live chat sessions.** Prefer
  `station/scripts/openclaw-prod gateway status` over restarting to answer
  questions.
- **Proton Pass uses machine-specific identities.** The Mac uses `codex-mac`
  and `claude-mac`; this Ubuntu account uses item-scoped `codex-utorm` and
  `claude-utorm` sessions through `codex-pass` and `claude-pass`. Never copy
  tokens, session directories, or private keys between machines. Agents have no
  `Personal` or whole-vault access. Secrets are injected only into the child
  process that needs them, with an audit reason and masking. The separate
  `openclaw-prod-utorm` identity is staged deny-all and has no session under
  `caio`; production provisioning requires an operator-authenticated handoff to
  the isolated `openclaw` Linux account.

## Procedures

**Read [`docs/procedures/00-conventions.md`](docs/procedures/00-conventions.md)
before your first commit.** It is eight short sections and it is the rule, not
background reading. What follows is an index of it, not a substitute — an
earlier version of this file summarised four of the eight sections as "the short
version", and an agent that trusted the summary broke both rules the summary
omitted.

1. **Userspace over sudo, always** (§2). When root is genuinely required, do not
   attempt it — write the exact command into the record's
   `outputs/needs-sudo.md` and hand it to Caio.
2. **Every non-trivial change gets a dated record** in `records/<date>-<slug>/`
   with real evidence in `work/` (§3). Actual logs and command output. A record
   that only asserts "installed X" does not meet the bar.
3. **Verify before claiming** (§3). Record the command *and* its output. Say
   plainly when something failed or was skipped. Records describe what is true,
   not what was intended — and get corrected when reality moves past them.
4. **Each capability gets `layers/<name>/AGENTS.md`** (§4, §5), linked from here.
   Agent files describe what is true about this machine, never aspirations.
5. **Secrets stay in the tool's own config under `$HOME`** (§6), never in this
   repo. Run `station/scripts/scan-secrets.sh` before pushing anywhere.
6. **Know what belongs in git** (§7): `station/`, `memory/`, `docs/`, `layers/`,
   `records/`. Never `~/.claude.json`, `~/.claude/.credentials.json`, or any
   `openclaw.json` — if it authenticates, it does not get committed.
7. **Prefer an assertion to a remembered step.** `check-gateway-isolation.sh`
   and `check-unit-paths.sh` under `station/scripts/` are read-only, need no
   sudo, and run as part of `sync-context.sh`. If you find yourself documenting
   "remember to rewrite X on deploy", write the check instead.
8. **Run `station/scripts/sync-context.sh` before committing** (§8). Claude
   authors memory at `~/.claude/projects/<project>/memory/`; `memory/` here is a
   snapshot that drifts unless you sync it. Tracked context that no longer
   matches the machine is worse than none, because the next agent believes it.

## Layout

```
workspace/
├── AGENTS.md              # this file (CLAUDE.md symlinks here)
├── docs/
│   ├── procedures/        # how we work
│   └── runbooks/          # how to operate a layer
├── layers/<name>/         # one dir per installed capability
└── records/<date>-<slug>/ # dated implementation records
```

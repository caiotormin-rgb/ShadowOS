# Working Conventions — torm

Established 2026-08-22. These are the procedures agents and humans follow in this
workspace. They are deliberately short; if a rule needs a page of explanation it
is the wrong rule.

## 1. The station

`torm` is the Ubuntu workstation this repo lives on.

| Fact | Value |
|---|---|
| Hostname | `torm` |
| OS | Ubuntu 24.04.4 LTS |
| Kernel | 7.0.0-30-generic |
| Primary user | `caio` |
| LAN address | `<lan-ip>/22` (`enp2s0`) |
| sshd | active + enabled, listening `0.0.0.0:22` |
| Passwordless sudo | **No** — sudo prompts for a password |

`torm` is an SSH *target*, not an SSH client. There are no outbound SSH
credentials configured (`~/.ssh/` holds only an empty `authorized_keys`). Any
procedure that says "on the remote station" means *someone connects to torm*,
not that torm connects outward.

## 2. No-sudo default

Agents run without an interactive terminal, so `sudo` cannot be answered and will
hang or fail. **Install into userspace by default** — `~/.local`, `~/.openclaw`,
per-user npm prefixes, `systemctl --user`.

When a task genuinely requires root, do not attempt it. Write the exact command
into the record's `outputs/` as a `needs-sudo.md` handoff and tell the human. See
[the OpenClaw record](../../records/2026-08-22-openclaw-bootstrap/outputs/needs-sudo.md)
for the shape of that handoff.

## 3. Implementation records

Every non-trivial change gets a dated record directory:

```
records/<YYYY-MM-DD>-<slug>/
├── README.md      # what, why, outcome — written last, read first
├── work/          # logs, downloaded scripts, checksums, scratch
└── outputs/       # artifacts a human acts on (handoffs, configs, debs)
```

Rules:

- **`work/` is evidence, not decoration.** Save the actual installer, the actual
  log, the actual checksum. A record that only says "installed X" is not a record.
- **Verify before claiming.** Record the command *and* its output. If a step was
  skipped or failed, the README says so plainly.
- **Dates are absolute.** `2026-08-22`, never "today" or "last week".
- This mirrors the pre-existing `~/Documents/Codex/<date>/<task>/{work,outputs}`
  pattern already on this box, so the two are readable side by side.

## 4. Layers

A *layer* is a capability installed on torm that agents can use or must respect.
Each gets `layers/<name>/` containing at minimum an `AGENTS.md` describing what
the layer is, how to check its health, and what an agent must never do to it.

Current layers: [`openclaw`](../../layers/openclaw/AGENTS.md).

## 5. Agent context files

- `AGENTS.md` at the repo root is the entry point — every agent reads it first.
- `CLAUDE.md` is a symlink to `AGENTS.md`. One file, two names, no drift.
- Layer-specific context lives in `layers/<name>/AGENTS.md` and is linked from root.

Keep them factual. Agent files describe *what is true about this machine*, not
aspirations.

## 6. Secrets

API keys and channel tokens never enter this repo. They live in the tool's own
config under `$HOME` (e.g. `~/.openclaw/openclaw.json`, mode `0600`). `.gitignore`
blocks the obvious shapes, but the real rule is: if it authenticates, it does not
get committed.

## 7. What lives in git, and what must never

This repo is intended to be pushed to a **private** remote, so the boundary
matters.

**Tracked here:**

| Path | What |
|---|---|
| `station/bashrc.d/` | shell fragments; `~/.bashrc` sources them |
| `station/claude/CLAUDE.md` | copy of the machine-level `~/.claude/CLAUDE.md` hook |
| `station/codex/AGENTS.md` | copy of the machine-level `~/.codex/AGENTS.md` hook |
| `station/systemd/user/` | unit files kept as reference. Anything sourced from Caio's account is named for it and suffixed `.disabled` so it cannot be installed — see [the runbook](../runbooks/openclaw.md#the-unit-in-this-repo-is-not-the-production-unit) |
| `station/scripts/` | `sync-context.sh`, `restore-context.sh`, `scan-secrets.sh`, `publish-check.sh`, and the `check-*.sh` assertions |
| `memory/` | snapshot of Claude's memory files |

**Never tracked** — these hold live credentials:

- `~/.claude.json` (contains `oauthAccount`)
- `~/.claude/.credentials.json` (contains `mcpOAuth`)
- `~/.openclaw/openclaw.json` (gateway token; later provider and channel secrets)

`.gitignore` blocks all three by name as defence in depth, plus the usual key,
token and `.env` shapes. Only `*.redacted` snapshots of such files belong here.

**Before pushing anywhere, run:**

```bash
station/scripts/scan-secrets.sh
```

It greps tracked files for credential patterns *and* for the live gateway token
read from `~/.openclaw/openclaw.json`. Exit 0 means clean. It has been
negative-tested against planted decoys — it is not a no-op.

## 8. Context sync

`memory/` is a **snapshot, not the live copy**. Claude authors memory at
`~/.claude/projects/<project>/memory/`; replacing that directory with a symlink
was rejected as too invasive to Claude's own state. So the two can drift.

```bash
station/scripts/sync-context.sh      # live machine state -> repo (run before committing)
station/scripts/restore-context.sh   # repo -> a fresh machine (run after cloning)
```

`restore-context.sh` installs the Claude and Codex user-level hooks (backing up
existing files) and wires `~/.bashrc` to `station/bashrc.d/`. It installs **no
software** — use the runbooks for that.

`sync-context.sh` also runs the two boundary assertions and refuses to finish if
either fails, because the thing it snapshots is exactly the thing that can break
them:

```bash
station/scripts/check-gateway-isolation.sh   # the gateway still runs as `openclaw`
station/scripts/check-unit-paths.sh          # no tracked unit names /home/caio
```

Both are read-only, need no sudo, and are negative-tested against planted
decoys. Prefer an assertion to a documented step: a deploy step a human must
remember is a deploy step that eventually does not happen — see
[`records/2026-08-25-mail-context-unit-paths`](../../records/2026-08-25-mail-context-unit-paths/README.md),
where a unit sat two releases behind its deployed copy for exactly that reason.

Note `~/.bashrc` only applies to interactive shells, because stock Ubuntu returns
early when non-interactive. Scripts and systemd units do not get PATH from it;
the OpenClaw unit hardcodes its own.

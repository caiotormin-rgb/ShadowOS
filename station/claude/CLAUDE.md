# torm — user-level context

This file is read by Claude Code in **every** project on this machine. It is a
pointer, deliberately short. The canonical context is the workspace repo.

## Read this first

**`~/workspace/AGENTS.md`** — the torm operations repo. Read it before doing
work on this machine, even when the current project is somewhere else.


## The two facts that bite hardest

1. **No passwordless sudo on torm.** Agent sessions have no terminal to type a
   password into, so `sudo` hangs or fails. Install to userspace (`~/.local`,
   per-tool prefixes, `systemctl --user`). When root is genuinely required, do
   not attempt it — write the command into a `needs-sudo.md` handoff and tell
   Caio.

2. **torm *is* the Ubuntu station.** It runs sshd and is an SSH *target*; it has
   no outbound SSH credentials. "The remote station" means torm.

## Also true

- torm hosts **OpenClaw**, a chat→agent gateway on `127.0.0.1:18789`, running
  as the isolated `openclaw` account from `/home/openclaw/.openclaw`. A second
  agent runtime can act on this filesystem, triggered from chat apps. Don't bind
  port 18789. Never print `/home/openclaw/.openclaw/openclaw.json`. Caio's own
  `~/.openclaw` rollback copy was removed 2026-09-13.
- **"Workspace" is ambiguous here.** `~/workspace` is the ops repo;
  `/home/openclaw/.openclaw/workspace` is OpenClaw's own agent workspace with
  its own `AGENTS.md`. Disambiguate before acting.
- Since 2026-09-13 there **is** a system-wide node/npm (`nodejs` 24.21 from the
  NodeSource apt repo, installed by hand). Production does not use it — it
  vendors Node under `/home/openclaw/.openclaw/tools/`.
- `~/.bashrc` only applies to interactive shells (stock Ubuntu early-return
  guard), so `openclaw` is not on PATH in scripts or systemd units.

## Conventions

Every non-trivial change gets a dated record in `~/workspace/records/<date>-<slug>/`
with real evidence in `work/`. Secrets never enter the repo — they stay in each
tool's own config under `$HOME`. Details in
`~/workspace/docs/procedures/00-conventions.md`.

## Proton Pass and machine boundary

This account has Proton Pass CLI at `~/.local/bin/pass-cli` and a separate,
item-scoped `claude-utorm` session. Use `claude-pass`; do not call the unscoped
CLI for agent work. `claude-pass run` requires a human-readable audit reason and
an env file containing only `pass://` references, then injects values into the
child process with masking. Run `claude-pass canary` to verify the path.

Never copy the Mac's access tokens, session state, or private keys here. Never
request access to `Personal`, a whole vault, or a raw secret value. New grants
are item-level, viewer by default, and require Caio's approval. Read
`~/workspace/AGENTS.md` for the authoritative machine boundary.

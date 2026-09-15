# torm — user-level Codex context

This file is read by Codex in every project on this Ubuntu station. The
authoritative machine context is `~/workspace/AGENTS.md`; read it before doing
work here, even when the current project is elsewhere.

## Machine boundary

- This machine is the Ubuntu station named `torm`; the Mac reaches it through
  the SSH alias `utorm`.
- There is no passwordless sudo. Do not attempt or loop on sudo; record a human
  handoff when root is genuinely required.
- `/home/openclaw/.openclaw/` is isolated production state. Do not print, copy,
  or hand-edit its credential-bearing configuration.
- You may not be the only agent writing here. Inspect Git and process state,
  preserve unrelated work, and follow the dated-record procedure.

## Proton Pass credentials

This account has Proton Pass CLI at `~/.local/bin/pass-cli` and a separate,
item-scoped `codex-utorm` session. Use `codex-pass`; do not call the unscoped
CLI for agent work. `codex-pass run` requires a human-readable audit reason and
an env file containing only `pass://` references, then injects values into the
child process with masking. Run `codex-pass canary` to verify the path.

Never copy the Mac's access tokens, session directories, or private keys here.
Never request access to `Personal`, a whole vault, or a raw secret value. New
grants are item-level, viewer by default, and require Caio's approval.

Secrets never enter Git, chat, logs, instructions, command arguments, or agent
memory. Missing access is an intended deny-by-default state.

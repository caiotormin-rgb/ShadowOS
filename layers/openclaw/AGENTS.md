# Layer: OpenClaw

Installed on torm 2026-08-22. Version **2026.7.1-2** (`0790d9f`).

## What it is

OpenClaw is a self-hosted gateway that connects chat apps — WhatsApp, Telegram,
Signal, Discord, Slack, iMessage and others — to AI agents. One long-lived
Gateway process owns the provider connections and exposes a WebSocket API;
clients (CLI, web dashboard) and nodes attach to it. Upstream project by Peter
Steinberger; formerly Warelay, then Moltbot, renamed OpenClaw in January 2026.

Docs: <https://docs.openclaw.ai/> · Source: <https://github.com/openclaw/openclaw>

## How it is installed here

Production runs deliberately under the isolated `openclaw` Linux account. The
account has no login shell, sudo, or supplemental groups; its home is mode
`0700`. Caio remains the human operator through explicit sudo commands.

| Thing | Path / value |
|---|---|
| Operator wrapper | `/home/caio/workspace/station/scripts/openclaw-prod` |
| CLI entry point | `/home/openclaw/.openclaw/bin/openclaw` |
| Bundled Node | `/home/openclaw/.openclaw/tools/node-v24.15.0` |
| Config | `/home/openclaw/.openclaw/openclaw.json` |
| State | `/home/openclaw/.openclaw/` |
| Sessions | `/home/openclaw/.openclaw/agents/main/sessions` |
| Service | `/home/openclaw/.config/systemd/user/openclaw-gateway.service` (mode 0700 — **not** the file in `station/systemd/user/`) |
| Logs | production user journal (`openclaw-gateway.service`) |
| Port | `18789`, **loopback only** |
| Dashboard | <http://127.0.0.1:18789/> |

Node 24.15.0 is vendored under `/home/openclaw/.openclaw/tools/` and is **not**
on the system PATH. Since 2026-09-13 torm also has a system-wide `nodejs` 24.21
(NodeSource apt repo, installed by hand); production does not use it.

In Caio's interactive shell, `openclaw` is a function that calls the wrapper, so
it **operates production**. Caio's rollback bootstrap under `/home/caio/.openclaw`
was removed on 2026-09-13 (archive and evidence in
`records/2026-09-13-remove-caio-openclaw-rollback/`). The wrapper prompts an
interactive human for sudo and fails closed for non-interactive agents because
torm intentionally has no passwordless sudo.

## The agent can read Caio's mail

Since 2026-08-23 the production Gateway has a registered MCP server,
`mail-context`, exposing four read-only tools: `mail_search`, `mail_thread`,
`mail_candidates`, `mail_status`. They answer from a local SQLite index of 24
months of Gmail metadata, not from Gmail directly.

| Thing | Path |
|---|---|
| Deployed code | `/home/openclaw/mail-context/` (copy of `layers/openclaw/mail-context/`) |
| Credentials | `/home/openclaw/.config/mail-context/` — client secret, token, encryption waiver |
| Index | `/home/openclaw/.local/state/mail-context/mail-context.sqlite` (~82 MiB) |
| Timer | `mail-context-sync.timer` under the `openclaw` user manager, 07:10 / 19:10 |

Operations and recovery: [`docs/runbooks/mail-context.md`](../../docs/runbooks/mail-context.md).

What this changes for you:

- **The index is a cache, never authoritative.** Gmail is. Deleting it costs
  ~30 minutes of resync and nothing else.
- **There is no send path and it must stay that way.** The grant is
  `gmail.readonly`; the transport allowlists read-only paths and an AST test
  asserts every HTTP call is a GET. Gmail has no draft-without-send scope, so
  adding drafting means widening the grant to one that also permits sending.
  The tests are the boundary — do not relax them for convenience.
- **`PYTHONPATH` is stripped from MCP stdio servers** by the Gateway for startup
  safety. The server works because `--cwd` puts the package on `sys.path`. Do
  not rely on env vars reaching a spawned MCP server.
- **The index is unencrypted at rest** and the preflight gate passes only
  through a recorded operator waiver. `calendar-context` and `drive-context`
  exist in this repo but have no credentials, partly because that waiver does
  not cover the attendee addresses Calendar would store.

## ⚠ Two "workspaces", two AGENTS.md

The gateway generates **its own** agent workspace at
`/home/openclaw/.openclaw/workspace/` —
a separate (currently uncommitted) git repo containing `AGENTS.md`, `SOUL.md`,
`IDENTITY.md`, `USER.md`, `TOOLS.md`, `BOOTSTRAP.md`, `HEARTBEAT.md`. Those are
OpenClaw's instructions to *its own chat agent*. They are not ours.

| Path | Whose | Audience |
|---|---|---|
| `/home/caio/workspace/AGENTS.md` | this repo | Claude Code / dev agents |
| `/home/openclaw/.openclaw/workspace/AGENTS.md` | OpenClaw | production chat agent |

Do not edit OpenClaw's copies to steer dev agents, do not copy ours over theirs,
and when someone says "the workspace" on torm, disambiguate before acting. Note
`BOOTSTRAP.md` instructs the agent to delete itself after first run — expect that
file to vanish once a provider is configured and the agent runs.

## Runtime-generated state

Only `bin/`, `tools/`, `openclaw.json` and `IMPLEMENTATION.md` came from the
install. Everything else under `/home/openclaw/.openclaw/` is live or
runtime-generated state:
`state/openclaw.sqlite` (gateway state), `agents/main/agent/` (agent DB),
`agents/main/sessions/`, `identity/device.json`, `logs/config-audit.jsonl`
(audit trail of config changes), `skill-workshop/`, `workspace-attestations/`,
and `workspace/`. Config edits also leave `openclaw.json.bak*` and
`openclaw.json.last-good` rollback copies alongside the live config.

`tools/node-v24.15.0` is 594M — about 99.7% of the install's 596M footprint.

## Security posture

- **Bind: loopback.** Listening on `127.0.0.1:18789` and `[::1]:18789` only.
  Verified unreachable from `<lan-ip>`.
- **Auth: token.** Production rejects Caio's rollback token, confirming the two
  credential homes are separated. Auth applies even on loopback.
- Do **not** flip `gateway.bind` to `lan`, or enable Tailscale funnel, without
  the human explicitly asking. For remote access the documented route is a
  Tailscale tailnet or an SSH tunnel:
  `ssh -N -L 18789:127.0.0.1:18789 caio@torm` — note the tunnel does **not**
  bypass auth; the client still sends the token.

## Rules for agents

1. **Never print `/home/openclaw/.openclaw/openclaw.json`** or any value under
   `gateway.auth`, `channels.*.token`, or `credentials/`.
2. **Never hand-edit the config.** Use `openclaw config set <path> <value>`.
3. **Do not bind port 18789.**
4. **Do not restart the gateway to "check" something** — restarting drops live
   chat sessions. `station/scripts/openclaw-prod gateway status` answers most
   questions read-only when an operator can authenticate.
5. **Treat `/home/openclaw/.openclaw/` as live state**, not scratch space.
6. Remember OpenClaw can act on this filesystem on its own, triggered from chat.

## Health check

```bash
station/scripts/openclaw-prod gateway status
station/scripts/openclaw-prod doctor
station/scripts/openclaw-prod security audit --deep
```

## Current verified state — 2026-08-23

- The production Gateway is in the `openclaw-gateway.service` cgroup and listens
  only on loopback port `18789`.
- The `openclaw` account has no login shell, sudo, or supplemental groups; its
  home is mode `0700` and `Linger=yes`.
- Caio's bootstrap Gateway was **removed 2026-09-13** (unit and `~/.openclaw`);
  there is no rollback install. Archive: `~/archive/2026-09-13-caio-openclaw-rollback.tar.gz`.
- **The unit tracked in this repo is Caio's bootstrap, not production** (fixed
  2026-08-25; it was previously filed under the production filename). It is
  `station/systemd/user/caio-bootstrap-gateway.service.disabled` and must never
  be installed — deploying it would give the gateway `HOME=/home/caio` and erase
  the account boundary. Assertions: `station/scripts/check-gateway-isolation.sh`.
- Production provider, credential-file, owner, and channel health still require
  an operator-authenticated check. Do not infer them from Caio's bootstrap CLI.
- Telegram work is explicitly deferred while the operator is mobile. The
  production journal also reports a stopped WhatsApp sidecar; do not activate or
  repair any channel without a separate, deliberate channel-state check.

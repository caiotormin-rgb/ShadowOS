# Runbook — OpenClaw on torm

Operating guide for the [OpenClaw layer](../../layers/openclaw/AGENTS.md).
Production runs as the non-login `openclaw` account. From an interactive shell
as Caio, plain `openclaw` already calls the repository wrapper — **every
`openclaw …` typed there acts on production**, including `doctor --fix`. Caio's
rollback bootstrap was removed on 2026-09-13. Explicitly:

```bash
station/scripts/openclaw-prod status --deep
```

From the Mac, allocate a TTY so sudo can prompt locally on torm:

```bash
ssh -t torm '/home/caio/workspace/station/scripts/openclaw-prod status --deep'
```

Never send the sudo password, provider credential, Gateway token, or channel
token through chat. Non-interactive use fails closed because passwordless sudo
is intentionally disabled.

## Daily operations

| Task | Command |
|---|---|
| Health / bind / version | `station/scripts/openclaw-prod gateway status` |
| Full audit | `station/scripts/openclaw-prod doctor` |
| Deep security audit | `station/scripts/openclaw-prod security audit --deep` |
| Model/auth status | `station/scripts/openclaw-prod models status --json` |
| Service state | `station/scripts/openclaw-prod gateway status` |
| Restart (drops sessions) | `station/scripts/openclaw-prod gateway restart` |
| Stop | `station/scripts/openclaw-prod gateway stop` |
| Dashboard | <http://127.0.0.1:18789/> |

## Production checkpoint — 2026-08-23

The isolated Gateway is running as `openclaw`, loopback-only, with boot
persistence enabled. Caio's older bootstrap is disabled and inactive. Do not
copy state again or start the bootstrap: stabilize the existing production
instance in place.

The remaining checks need operator authentication. Telegram is deliberately
deferred while the operator is mobile.

### 1. Model provider and credential validation

Inspect before changing anything:

```bash
station/scripts/openclaw-prod models status --json
station/scripts/openclaw-prod doctor
```

If provider authentication is expired, reauthenticate through the supported
interactive provider flow as `openclaw`. Do not copy Mac or Caio credential
files into production. Credentials must remain under
`/home/openclaw/.openclaw/` and must not be committed anywhere.

### 2. Command owner (required before pairing anyone)

The current production value is not yet verified. The command owner is the
human account allowed to run owner-only commands (`/diagnostics`, `/config`,
`/export-trajectory`) and approve dangerous actions. **DM pairing does not make
someone the owner** — an unpaired-but-unowned bot is a bot nobody can govern.

Verify this in the production config before exposing a channel. Any change and
the required restart are deferred until the operator is at a terminal.

```bash
station/scripts/openclaw-prod config get commands.ownerAllowFrom
```

### 3. Channels (interactive and currently deferred)

Pairing is interactive — QR scan for WhatsApp, bot token for Telegram/Discord.
Do it from a terminal you control:

```bash
station/scripts/openclaw-prod config --section channels
```

Review the allowlist afterwards. A channel with no allowlist will talk to anyone
who messages it.

### 4. Boot persistence

`openclaw` has `Linger=yes`, and the live Gateway belongs to its
`openclaw-gateway.service` cgroup. Verify reboot persistence during a planned
maintenance window; do not restart merely to test it.

## The unit in this repo is not the production unit

`station/systemd/user/caio-bootstrap-gateway.service.disabled` is a snapshot of
**Caio's bootstrap gateway** — the disabled rollback under uid 1000. Production
is a different unit owned by the `openclaw` account at
`/home/openclaw/.config/systemd/user/openclaw-gateway.service`, which is mode
0700 and cannot be read from Caio's account without root.

Do not "fix" the repo file by deploying it. Production runs as uid 1001 out of
`/home/openclaw`; `openclaw` shares no group with `caio` and `/home/caio` is
`drwxr-x---`, so the gateway cannot traverse Caio's home. That kernel boundary
is load-bearing — the gateway answers WhatsApp and Telegram, and a plaintext
personal mail archive sits in `~/archive` precisely because the gateway cannot
reach it. Installing
the bootstrap unit would give the gateway `HOME=/home/caio` and erase that
silently.

Two things keep that from happening by accident, rather than by remembering:

- the filename ends in `.disabled`, which systemd rejects as a unit name and a
  `*.service` glob does not match;
- `station/scripts/check-gateway-isolation.sh` asserts the boundary and fails if
  any *loadable* unit in this repo would run the gateway from `/home/caio`.
  `sync-context.sh` runs it and refuses to finish if it fails.

```bash
station/scripts/check-gateway-isolation.sh
```

It is read-only and needs no sudo. Note it deliberately does **not** follow the
`sed`-rewrite-on-deploy pattern that `docs/runbooks/mail-context.md` used to
prescribe for its own unit: a rewrite that a human must remember is the same
hazard in a different place. That pattern was removed from mail-context on
2026-08-25 — its unit now uses systemd's `%h` and deploys with a plain `cp`, and
`station/scripts/check-unit-paths.sh` fails if any tracked unit reintroduces a
literal home path.

## Remote access

The gateway is loopback-only by design. To reach the dashboard from another
machine, tunnel — do not rebind:

```bash
ssh -N -L 18789:127.0.0.1:18789 caio@torm
```

Then open <http://127.0.0.1:18789/> locally. The tunnel does **not** bypass
gateway auth — the client still presents the token from `gateway.auth.token`.

Do not change the production bind while stabilizing. **Never** use Tailscale
*funnel* or `gateway.bind lan` without a deliberate decision.

## Upgrades

Do not reuse Caio's userspace installer for production. An upgrade must run in
the isolated account context, preserve a verified backup, name the pinned
version, and be separately approved. Record it per
[conventions](../procedures/00-conventions.md#3-implementation-records).

## Uninstall

Do not uninstall production during stabilization. An uninstall must name the
`openclaw` account and exact state path, stop the one active Gateway, preserve a
verified backup, and be separately approved. Never reuse the old generic
`rm -rf ~/.openclaw` sequence from Caio's bootstrap context.

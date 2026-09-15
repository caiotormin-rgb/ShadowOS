# Proton Pass agent credentials on utorm

Date installed: 2026-08-24

## Boundary

Ubuntu uses its own Proton identities and isolated session directories. Nothing
is copied from the Mac. The `caio` account has two coding-agent identities:

- `codex-utorm`
- `claude-utorm`

Each starts with viewer access only to the synthetic `Agent Injection Test`
item. Neither has access to `Personal` or to an entire vault. Grant new items
individually and use viewer unless the workflow truly needs to update them.

`openclaw-prod-utorm` is a separate deny-all identity for the isolated
`openclaw` Linux account. It has no deployed session. Do not bootstrap it under
`caio`; an operator must authenticate the production-account handoff.

## Commands

```bash
codex-pass doctor
codex-pass canary
claude-pass doctor
claude-pass canary
```

For a real tool, create a local mode-0600 env reference file containing only
Proton references, never values:

```text
SERVICE_TOKEN=pass://SHARE_ID/ITEM_ID/field.name
```

Then inject it into one child process with an audit reason:

```bash
codex-pass run "Read service metadata" --env-file ~/.config/torm/pass-refs/service.env -- command args
```

The wrapper rejects common raw-token shapes and direct `--show-secrets` use.
The reason is recorded by Proton's agent monitor.

## Runtime state

- CLI: `~/.local/bin/pass-cli` (official Proton Pass CLI 2.3.2)
- Sessions: `~/.local/state/proton-pass-agents/<identity>/`
- Local references: `~/.config/torm/pass-refs/`
- Tracked wrapper: `~/workspace/station/scripts/agent-pass`
- Wrapper links: `~/.local/bin/{codex-pass,claude-pass}`

Session state and reference IDs remain outside Git. The session is persistent;
the current identities expire late on 2027-08-24 America/New_York
(2027-08-25 UTC). Renew before expiry, then replace only the matching identity's
session.

## Grant procedure

From an authenticated human Proton CLI session on the control machine:

```bash
pass-cli agent access grant AGENT --vault-name "Agent Secrets" --item-title "ITEM" --role viewer
```

Do not use `--vault` at agent creation and do not grant `Personal`. For a
credential needed by both coding agents, grant the same item to each identity;
do not share session state between them.

## Recovery

`station/scripts/restore-context.sh` restores the wrapper links and agent
instructions, but deliberately does not install software or restore sessions.
A missing/expired session must be renewed and logged in by a human administrator
without printing the personal access token.

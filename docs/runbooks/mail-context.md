# Runbook: mail-context

A rebuildable local index of Gmail metadata. Gmail stays authoritative; this is
a cache that can be deleted and rebuilt at any time without losing anything.

Implementation: [`layers/openclaw/mail-context/`](../../layers/openclaw/mail-context/).
Design: [`docs/plans/openclaw-google-mail-context.md`](../plans/openclaw-google-mail-context.md).

## What is live as of 2026-08-23

| | |
|---|---|
| OAuth client | Desktop app, project `<gcp-project>`, published to production |
| Scope | `gmail.readonly` **only** — there is no send or modify capability |
| Credentials | `/home/openclaw/.config/mail-context/{client_secret,token}.json`, mode 0600 |
| Index | `/home/openclaw/.local/state/mail-context/mail-context.sqlite`, mode 0600 |
| Window | 730 days (`sync.DEFAULT_WINDOW_DAYS`) |
| Runs as | the isolated **`openclaw`** service account |
| Deployed code | `/home/openclaw/mail-context/` (a copy; this repo is the source of truth) |
| Timer | **Installed and enabled**, twice daily at 07:10 / 19:10 |
| Agent access | Registered MCP server `mail-context`, four read-only tools |

Measured: 64,192 messages in the 24-month window, ~53 msg/s with 12 workers,
roughly 20 minutes for a full load and ~67 MiB on disk.

## Daily operations

Everything runs as the service account. `caio` no longer holds the token.

```bash
S="sudo -u openclaw env HOME=/home/openclaw PYTHONPATH=/home/openclaw/mail-context python3"
$S -m mailctx.syncrun --status      # what is indexed, and how stale
$S -m mailctx.syncrun               # incremental sync
$S -m mailctx.authorize --status    # token state, nothing sensitive printed
```

Timer and service:

```bash
U="sudo -u openclaw XDG_RUNTIME_DIR=/run/user/$(id -u openclaw) systemctl --user"
$U list-timers mail-context-sync.timer
$U start mail-context-sync.service
sudo -u openclaw XDG_RUNTIME_DIR=/run/user/$(id -u openclaw) journalctl --user -u mail-context-sync -n 20 --no-pager
```

If a start appears to hang, the unit is probably in `activating (auto-restart)`
from an earlier failure — `systemctl start` blocks until the backoff completes.
Clear it with `$U reset-failed mail-context-sync.service`.

A long initial load is safe to interrupt. It skips what is already stored, so
re-running resumes rather than restarting.

## Redeploying after a code change

The repository is the source of truth; `/home/openclaw/mail-context` is a copy.

```bash
sudo cp -r layers/openclaw/mail-context/{mailctx,schema.sql} /home/openclaw/mail-context/
sudo chown -R openclaw:openclaw /home/openclaw/mail-context
/home/caio/workspace/station/scripts/openclaw-prod mcp reload
```

If the systemd unit changed, recopy it. **There is no path rewrite any more**
— since 2026-08-25 the tracked unit uses systemd's `%h`, which expands to the
home of whichever account's user manager loads it, and `mailctx` resolves its
config and state from `Path.home()`. Copy it verbatim:

```bash
sudo cp layers/openclaw/mail-context/systemd/mail-context-sync.{service,timer} /home/openclaw/.config/systemd/user/
sudo chown openclaw:openclaw /home/openclaw/.config/systemd/user/mail-context-sync.{service,timer}
sudo -u openclaw XDG_RUNTIME_DIR=/run/user/$(id -u openclaw) systemctl --user daemon-reload
```

The old `sed -i` rewrite is deleted, not moved. It was the reason the copy in
Caio's `~/.config/systemd/user/` sat two releases behind the repo, still
carrying both directives the next section says must never come back. A deploy
step a human must remember is a deploy step that eventually does not happen —
`station/scripts/check-unit-paths.sh` now fails if any tracked unit reintroduces
a literal home path. See
[`records/2026-08-25-mail-context-unit-paths`](../../records/2026-08-25-mail-context-unit-paths/README.md).

Two directives must never come back: `ProtectKernelModules=` fails any
unprivileged `--user` unit with `218/CAPABILITIES`, and `Restart=` on a
timer-triggered oneshot both ignores the schedule and makes a failed unit hang
the next manual start.

And one directive does less than it looks like it does. Measured on torm
2026-08-25: `ProtectSystem=strict` is effective in this `--user` manager (a
write to `/usr` is blocked), but **`ProtectHome=read-only` is not** — a unit
carrying it still wrote freely to `$HOME`, with and without `ReadWritePaths=`.
It is kept because it costs nothing and does apply under a system manager, but
do not read the hardening block as confining the sync to two paths. It does not.
Evidence: `records/2026-08-25-mail-context-unit-paths/work/protecthome-isolation.log`.

Logs: `journalctl --user -u mail-context-sync -n 50`. They contain IDs, counts,
durations, and error classes only — never subjects, snippets, or addresses.

## Recovery

**Stale sync cursor.** Gmail keeps roughly 30 days of history. A cursor older
than that returns 404, which the syncer treats as expected: it falls back to a
bounded resync automatically. Nothing to do.

**Authorization failed.** `status` reports `error_class: auth`. The last good
index is preserved. Re-run the consent flow:

```bash
python3 -m mailctx.authorize --port 8765 --no-browser   # then ssh -L 8765:127.0.0.1:8765 torm
```

**Rebuild from scratch.** The index holds nothing authoritative:

```bash
rm ~/.local/state/mail-context/mail-context.sqlite*
python3 -m mailctx.syncrun
```

**Revoke access entirely.**

```bash
systemctl --user disable --now mail-context-sync.timer
rm ~/.config/mail-context/token.json
rm ~/.local/state/mail-context/mail-context.sqlite*
```

Then remove the app's access at <https://myaccount.google.com/permissions>.
Deleting the OAuth client in Google Cloud also works. Nothing in Gmail changes.

## Two things to know before changing this

**The encryption gate is waived, not passed.** torm's root filesystem is plain
ext4 with no LUKS and a plaintext swapfile, so the index is readable by anyone
with offline access to the disk. The waiver at
`~/.config/mail-context/encryption-waiver.txt` records the operator's reasoning
and is surfaced in every sync run. Delete it after enabling full-disk
encryption and the gate will pass on its own merits.

**Sending is absent by construction, and tests hold it that way.** Gmail has no
draft-without-send scope, so a future drafting phase must widen the grant to
`gmail.compose` — which also authorizes sending. The transport allowlist and
the AST check in `tests/test_gmail_ceiling.py` are what keep that safe. Do not
weaken them to make a feature easier.

## Not done yet

- Candidate detection and proactive drafting (plan phases 4 and 5) do not exist.
  `action_candidates` and `draft_links` exist in the schema and stay empty.
- Calendar and Drive have implementations but no credentials, so neither can
  reach real data.

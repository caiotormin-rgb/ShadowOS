#!/usr/bin/env bash
# Sync machine-level agent context INTO the repo so it is tracked.
# Memory is authored live by Claude at ~/.claude/projects/<proj>/memory/;
# this snapshots it. Run before committing if memory has changed.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

MEM="$HOME/.claude/projects/-home-caio/memory"
[ -d "$MEM" ] && { mkdir -p memory; rsync -a --delete "$MEM"/ memory/; echo "memory/ synced from $MEM"; } \
              || echo "skip: $MEM not present"

[ -f "$HOME/.claude/CLAUDE.md" ] && { mkdir -p station/claude; cp "$HOME/.claude/CLAUDE.md" station/claude/CLAUDE.md; echo "station/claude/CLAUDE.md synced"; }

[ -f "$HOME/.codex/AGENTS.md" ] && { mkdir -p station/codex; cp "$HOME/.codex/AGENTS.md" station/codex/AGENTS.md; echo "station/codex/AGENTS.md synced"; }

# This runs as caio, so $HOME/.config/systemd/user holds Caio's *bootstrap*
# gateway — the disabled rollback under uid 1000 — not production. Production
# belongs to the `openclaw` account and lives under /home/openclaw (mode 0700,
# unreadable here). Snapshotting it under the production filename is what
# produced the drift fixed on 2026-08-25, so the destination name says whose
# unit it is and ends in `.disabled`, which systemd refuses to load.
SRC="$HOME/.config/systemd/user/openclaw-gateway.service"
DST="station/systemd/user/caio-bootstrap-gateway.service.disabled"
if [ -f "$SRC" ]; then
  mkdir -p station/systemd/user
  # Keep the existing DO-NOT-INSTALL header; refresh only the unit body below it.
  if [ -f "$DST" ]; then
    sed -n '1,/^\[Unit\]/p' "$DST" | sed '$d' > "$DST.tmp"
  else
    : > "$DST.tmp"
  fi
  cat "$SRC" >> "$DST.tmp"
  mv "$DST.tmp" "$DST"
  echo "caio bootstrap unit snapshotted -> $DST (NOT the production unit)"
fi

# The boundary that snapshot must never quietly break.
station/scripts/check-gateway-isolation.sh || {
  echo "sync-context: gateway isolation check FAILED — fix before committing" >&2
  exit 1
}

echo
station/scripts/check-unit-paths.sh || {
  echo "sync-context: a tracked unit names /home/caio — fix before committing" >&2
  exit 1
}

git status --short

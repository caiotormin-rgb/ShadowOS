#!/usr/bin/env bash
# Apply this repo's context to a machine (fresh clone / new station).
# Does NOT install software — see docs/runbooks/ for that.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
REPO="$(pwd)"

mkdir -p "$HOME/.claude"
if [ -e "$HOME/.claude/CLAUDE.md" ] && ! cmp -s station/claude/CLAUDE.md "$HOME/.claude/CLAUDE.md"; then
  cp "$HOME/.claude/CLAUDE.md" "$HOME/.claude/CLAUDE.md.bak.$(date +%s)"
  echo "backed up existing ~/.claude/CLAUDE.md"
fi
cp station/claude/CLAUDE.md "$HOME/.claude/CLAUDE.md"
echo "installed ~/.claude/CLAUDE.md"

mkdir -p "$HOME/.codex"
if [ -e "$HOME/.codex/AGENTS.md" ] && ! cmp -s station/codex/AGENTS.md "$HOME/.codex/AGENTS.md"; then
  cp "$HOME/.codex/AGENTS.md" "$HOME/.codex/AGENTS.md.bak.$(date +%s)"
  echo "backed up existing ~/.codex/AGENTS.md"
fi
cp station/codex/AGENTS.md "$HOME/.codex/AGENTS.md"
echo "installed ~/.codex/AGENTS.md"

mkdir -p "$HOME/.local/bin"
ln -sfn "$REPO/station/scripts/agent-pass" "$HOME/.local/bin/codex-pass"
ln -sfn "$REPO/station/scripts/agent-pass" "$HOME/.local/bin/claude-pass"
echo "installed codex-pass and claude-pass wrappers"

if [ -x "$HOME/.codex/plugins/.plugin-appserver/codex" ]; then
  ln -sfn "$HOME/.codex/plugins/.plugin-appserver/codex" "$HOME/.local/bin/codex"
  echo "exposed bundled Codex as ~/.local/bin/codex"
fi

if ! grep -q 'station/bashrc.d' "$HOME/.bashrc" 2>/dev/null; then
  cat >> "$HOME/.bashrc" <<EOF

# torm station config — tracked in $REPO/station/bashrc.d/
for _f in "$REPO"/station/bashrc.d/*.sh; do
  [ -r "\$_f" ] && . "\$_f"
done; unset _f
EOF
  echo "wired ~/.bashrc -> station/bashrc.d/"
else
  echo "~/.bashrc already wired"
fi
echo "done. agent hooks restored; memory/ remains a tracked Claude snapshot."

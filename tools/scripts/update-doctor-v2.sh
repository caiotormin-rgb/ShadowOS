#!/usr/bin/env bash
# Doctor search v2: web-first curation in the background (no config or grant changes).
# Rollback of the instructions: cp -p ~/backups/2026-09-14-doctor-v2/AGENTS.md.pre-v2 ~/.openclaw/workspace-shared-tools/AGENTS.md
# Stop background searches:     systemctl --user disable --now doctor-jobs.timer
set -euo pipefail

W=/home/openclaw/.openclaw/workspace/tools/doctor-search
SCRIPTS=/home/openclaw/tools/scripts
BACKUP=/home/openclaw/backups/2026-09-14-doctor-v2
AGENTS=/home/openclaw/.openclaw/workspace-shared-tools/AGENTS.md

echo "== 1/4 tests and build"
(cd "$W/core" && python3 -m unittest discover -s tests 2>&1 | tail -1)
(cd "$W/plugin" && npm run build >/dev/null && npx vitest run 2>&1 | grep -E "Tests ")

echo "== 2/4 agent instructions (doctor section replaced)"
umask 077
mkdir -p "$BACKUP"
cp -p "$AGENTS" "$BACKUP/AGENTS.md.pre-v2"
python3 - "$AGENTS" "$SCRIPTS/doctor-agents-section.md" <<'EOF'
import sys
path, section = sys.argv[1], open(sys.argv[2], encoding="utf-8").read()
text = open(path, encoding="utf-8").read()
marker = "\n# Finding a doctor"
if marker not in text:
    sys.exit("doctor section not found; AGENTS.md left unchanged")
text = text[: text.index(marker)].rstrip("\n") + "\n" + section
open(path, "w", encoding="utf-8").write(text)
print("updated")
EOF

echo "== 3/4 background search timer"
cp "$SCRIPTS"/systemd/doctor-jobs.service "$SCRIPTS"/systemd/doctor-jobs.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now doctor-jobs.timer
systemctl --user list-timers doctor-jobs.timer --no-pager | head -2

echo "== 4/4 gateway restart (loads the new tool actions)"
openclaw gateway restart
sleep 20
openclaw plugins inspect doctor-search-tool --runtime | grep -E "Status" || true
openclaw channels status 2>&1 | grep -E "WhatsApp|Telegram" || true

#!/usr/bin/env bash
# Connect doctor search to the household WhatsApp bot (agent shared-tools).
# Prepared 2026-09-13. Rollback (config + agent instructions + timers):
#   cp -p ~/backups/2026-09-14-doctor/openclaw.json.pre-doctor ~/.openclaw/openclaw.json
#   cp -p ~/backups/2026-09-14-doctor/AGENTS.md.pre-doctor ~/.openclaw/workspace-shared-tools/AGENTS.md
#   systemctl --user disable --now doctor-mail.timer doctor-retention.timer
#   openclaw gateway restart
set -euo pipefail

W=/home/openclaw/.openclaw/workspace/tools
SCRIPTS=/home/openclaw/tools/scripts
BACKUP=/home/openclaw/backups/2026-09-14-doctor
AGENTS=/home/openclaw/.openclaw/workspace-shared-tools/AGENTS.md

echo "== 1/7 tests and builds"
(cd "$W/doctor-search/core" && python3 -m unittest discover -s tests 2>&1 | tail -2)
(cd "$W/doctor-search/plugin" && npm run build >/dev/null && npx vitest run 2>&1 | grep -E "Tests ")
(cd "$W/access/plugin" && npm run build >/dev/null && npx vitest run 2>&1 | grep -E "Tests ")

echo "== 2/7 backups -> $BACKUP"
umask 077
mkdir -p "$BACKUP"
cp -p ~/.openclaw/openclaw.json "$BACKUP/openclaw.json.pre-doctor"
cp -p "$AGENTS" "$BACKUP/AGENTS.md.pre-doctor"

echo "== 3/7 access grants: doctor.request for every active person"
python3 - <<'EOF'
import os, sqlite3
c = sqlite3.connect(os.path.expanduser("~/.openclaw/access/access.sqlite3"))
with c:
    n = c.execute("""INSERT INTO grants (person_id, resource, action, scope_json)
        SELECT p.id, 'doctor', 'request', '{"self":false}' FROM people p
        WHERE p.status = 'active' AND NOT EXISTS (SELECT 1 FROM grants g WHERE g.person_id = p.id
          AND g.resource = 'doctor' AND g.action = 'request' AND g.status = 'active')""").rowcount
    c.execute("INSERT INTO audit (event, reason) VALUES ('grant', 'doctor.request for all active people')")
print("grants added:", n)
EOF

echo "== 4/7 config patch (plugin + shared-tools tool allow)"
PATCH=$(mktemp --suffix=.json5)
python3 - "$PATCH" <<'EOF'
import json, os, sys
cfg = json.load(open(os.path.expanduser("~/.openclaw/openclaw.json")))
plugins = cfg["plugins"]
paths = plugins["load"]["paths"]
doctor_path = "/home/openclaw/.openclaw/workspace/tools/doctor-search/plugin"
allow = plugins["allow"]
tools_allow = cfg["agents"]["entries"]["shared-tools"]["tools"]["allow"]
patch = {
    "plugins": {
        "load": {"paths": paths + ([doctor_path] if doctor_path not in paths else [])},
        "allow": allow + (["doctor-search-tool"] if "doctor-search-tool" not in allow else []),
        "entries": {"doctor-search-tool": {"enabled": True, "config": {
            "pythonPath": "/usr/bin/python3",
            "scriptPath": "/home/openclaw/.openclaw/workspace/tools/doctor-search/core/cli.py",
            "dbPath": "/home/openclaw/.openclaw/workspace/tools/doctor-search/core/data/doctor.sqlite3",
            "whatsappAccountId": "tools",
            "allowedRequesters": plugins["entries"]["grocery-list-tool"]["config"]["allowedRequesters"],
        }}},
    },
    "agents": {"entries": {"shared-tools": {"tools": {
        "allow": tools_allow + (["doctor_search"] if "doctor_search" not in tools_allow else []),
    }}}},
}
json.dump(patch, open(sys.argv[1], "w"))
print("patch written (numbers copied from the grocery plugin config, not printed)")
EOF
openclaw config patch --file "$PATCH" --dry-run >/dev/null
openclaw config patch --file "$PATCH"
rm -f "$PATCH"
openclaw config validate

echo "== 5/7 agent instructions"
python3 - "$AGENTS" "$SCRIPTS/doctor-agents-section.md" <<'EOF'
import sys
path, section_path = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
if "# Finding a doctor" in text:
    print("already present")
    raise SystemExit(0)
swaps = [
    ("Grocery (`grocery_list`)\n  is the only capability.",
     "Grocery (`grocery_list`)\n  and doctor search (`doctor_search`) are the only capabilities."),
    ("- Never send messages to a third party or alter an allowlist.",
     "- Never send messages to a third party or alter an allowlist. The one exception\n"
     "  is emailing a medical practice, which only happens after the person approves\n"
     "  the draft themselves with /ok."),
    ("say plainly that this account only does groceries.",
     "say plainly that this account only does groceries and finding doctors."),
]
for old, new in swaps:
    if old not in text:
        sys.exit(f"expected text not found, AGENTS.md left unchanged: {old[:50]!r}")
    text = text.replace(old, new)
text = text.rstrip("\n") + "\n" + open(section_path, encoding="utf-8").read()
open(path, "w", encoding="utf-8").write(text)
print("updated")
EOF

echo "== 6/7 timers"
mkdir -p ~/.config/systemd/user
cp "$SCRIPTS"/systemd/doctor-*.service "$SCRIPTS"/systemd/doctor-*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now doctor-mail.timer doctor-retention.timer
systemctl --user list-timers doctor-mail.timer doctor-retention.timer --no-pager | head -4

echo "== 7/7 gateway restart"
openclaw gateway restart
sleep 20
openclaw plugins inspect doctor-search-tool --runtime | grep -E "Status|Commands|^ok|Source" || true
openclaw channels status 2>&1 | grep -E "WhatsApp|Telegram" || true

#!/usr/bin/env bash
# Install the household access plugin in MONITOR mode (logs would-deny, blocks nothing).
# Prepared 2026-09-13. Backup of the config: ~/backups/2026-09-14-access/openclaw.json.pre-access
# Rollback: cp -p ~/backups/2026-09-14-access/openclaw.json.pre-access ~/.openclaw/openclaw.json && openclaw gateway restart
set -euo pipefail

PLUGIN=/home/openclaw/.openclaw/workspace/tools/access/plugin
PATCH=/home/openclaw/tools/scripts/access.patch.json5

echo "== 1/5 create store"
cd "$PLUGIN"
node --input-type=module -e "
import { initStore, DEFAULT_DB_PATH } from './dist/store.js';
initStore(DEFAULT_DB_PATH); console.log('store', DEFAULT_DB_PATH);
"

echo "== 2/5 seed owner + members (grocery.use); skipped if people already exist"
python3 - <<'EOF'
import json, os, re, sqlite3
db_path = os.path.expanduser("~/.openclaw/access/access.sqlite3")
cfg = json.load(open(os.path.expanduser("~/.openclaw/openclaw.json")))
owners = cfg["commands"]["ownerAllowFrom"]
digits = lambda p: "+" + re.sub(r"\D", "", p)
owner_wa = digits(next(o for o in owners if o.startswith("whatsapp:")))
owner_tg = next((o.split(":", 1)[1] for o in owners if o.startswith("telegram:")), None)
members = json.load(open(os.path.expanduser(
    "~/.openclaw/workspace/tools/grocery-list/core/config/members.json")))["members"]
conn = sqlite3.connect(db_path)
conn.execute("PRAGMA foreign_keys=ON")
if conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]:
    print("people already present; not seeding")
    raise SystemExit(0)
lang = lambda v: "pt" if str(v).lower().startswith("pt") else "en"

def add(name, role, lng, phone, telegram=None):
    pid = conn.execute("INSERT INTO people (name, role, lang) VALUES (?,?,?)", (name, role, lng)).lastrowid
    conn.execute("INSERT INTO identities (person_id, channel, account_id, sender_id) VALUES (?,?,?,?)",
                 (pid, "whatsapp", "", phone))
    if telegram:
        conn.execute("INSERT INTO identities (person_id, channel, account_id, sender_id) VALUES (?,?,?,?)",
                     (pid, "telegram", "", telegram))
    conn.execute("INSERT INTO grants (person_id, resource, action, scope_json) VALUES (?,?,?,?)",
                 (pid, "grocery", "use", '{"household":true}'))

with conn:
    seen_owner = False
    for m in members:
        phone = digits(m["phone"])
        is_owner = phone == owner_wa
        seen_owner |= is_owner
        add(m["name"], "owner" if is_owner else "member", lang(m.get("lang")), phone,
            owner_tg if is_owner else None)
    if not seen_owner:
        add("the owner", "owner", "en", owner_wa, owner_tg)
    conn.execute("INSERT INTO audit (event, reason) VALUES ('seed', 'initial import from members.json + ownerAllowFrom')")
for t in ("people", "identities", "grants"):
    print(t, conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
print("roles:", conn.execute("SELECT role, COUNT(*) FROM people GROUP BY role").fetchall())
allow = [digits(x) for x in cfg["channels"]["whatsapp"]["accounts"]["tools"]["allowFrom"]]
known = {r[0] for r in conn.execute("SELECT sender_id FROM identities WHERE channel='whatsapp'")}
print("tools allowlist numbers without a person:", sum(1 for a in allow if a not in known))
EOF
stat -c '%a %n' ~/.openclaw/access ~/.openclaw/access/access.sqlite3

echo "== 3/5 config patch"
openclaw config patch --file "$PATCH"
openclaw config validate

echo "== 4/5 gateway restart (drains in-flight turns, up to ~5 min)"
openclaw gateway restart

echo "== 5/5 check"
openclaw plugins inspect access --runtime | head -30

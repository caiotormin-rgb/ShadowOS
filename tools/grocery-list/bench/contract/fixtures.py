"""Synthetic fixture/snapshot helper. Receives only scratch paths from run.mjs."""
import json
import sqlite3
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / 'core'
sys.path.insert(0, str(CORE))
from db import connect
from groups import add_member
from people import remember_person
import agent_api

ALICE = '+15555550101'
BOB = '+15555550102'
CAROL = '+15555550103'
request = json.load(sys.stdin)
root = Path(request['root']).resolve()
# Deliberately only accept the runner-created temporary namespace.
if not root.name.startswith('grocery-contract-') or not (root / '.synthetic-fixture').is_file():
    raise SystemExit('not a synthetic fixture directory')
family = root / 'family.sqlite3'
if request['action'] == 'seed':
    with connect(family) as conn:
        add_member(conn, 'Synthetic family A', ALICE)
        add_member(conn, 'Synthetic family A', BOB)
        add_member(conn, 'Synthetic family B', CAROL)
        remember_person(conn, ALICE, request.get('lang', 'en'), 'Alice', 'Shop')
        remember_person(conn, BOB, 'pt', 'Bob', 'Shop')
        remember_person(conn, CAROL, 'en', 'Carol', 'Shop')
    for item in request.get('items', []):
        result = agent_api.handle(family, ALICE, {'action': 'add', 'store': 'Shop', 'items': [item]})
        assert result['ok'], result
    result = agent_api.handle(family, CAROL, {'action': 'add', 'store': 'Shop', 'items': [{'name': 'PRIVATE_SENTINEL_CAROL'}]})
    assert result['ok'], result
    print(json.dumps({'ok': True}))
else:
    def read(path):
        if not path.exists():
            return {'items': [], 'trips': [], 'preferences': []}
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as conn:
            conn.row_factory = sqlite3.Row
            return {
                'items': [dict(r) for r in conn.execute('SELECT i.name,i.quantity,i.unit,i.status,s.name AS store,g.name AS household FROM items i JOIN stores s ON s.id=i.store_id JOIN groups g ON g.id=s.group_id ORDER BY household,store,i.name,i.unit')],
                'trips': [dict(r) for r in conn.execute('SELECT s.name AS store,g.name AS household FROM trips t JOIN stores s ON s.id=t.store_id JOIN groups g ON g.id=s.group_id ORDER BY household,store')],
                'preferences': [dict(r) for r in conn.execute('SELECT p.display_name,p.lang,p.default_store,p.timezone,g.name AS household FROM people p LEFT JOIN group_members m ON m.actor=p.actor LEFT JOIN groups g ON g.id=m.group_id ORDER BY p.display_name,household')],
            }
    print(json.dumps({'family': read(family), 'private': [read(p) for p in sorted((root / 'private').glob('*.sqlite3'))]}))

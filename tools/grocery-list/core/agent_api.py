"""Compact member API. Identity and native-confirmation authority come from the bridge.

The legacy CLI remains an operator interface. This adapter owns one transaction,
including its retry receipt, while reusing the existing domain functions/renderers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import sqlite3
import time
from typing import Any
from zoneinfo import ZoneInfo

import activity
import member_render
import sections
import synonyms
from db import connect
from errors import GroceryError
from groups import group_for_actor, group_row
from insights import due
from items import (current_items, ingest_items, parse_items_json, partial_candidates,
                   remove_items, resolve_item, set_status)
from people import language_for, public_label, remember_person, set_timezone, timezone_for
from render import grouped_items, item_line, render_assumptions
from stores import resolve_store, store_row
from text import clean_text, normalized, parse_timestamp, unit_filter
from trips import close_trip, reopen_trip

CONFIRMATION_SECONDS = 300
RECEIPT_SECONDS = 86400
ACTIONS = frozenset('add list buy unbuy remove confirm_remove close reopen history stores due layout help onboard activity preferences'.split())


class _Transaction:
    """Domain helpers may commit; the API commits only after storing its receipt."""
    def __init__(self, conn):
        self.conn = conn

    def __getattr__(self, key):
        return getattr(self.conn, key)

    def commit(self):
        pass


class _ReplyError(Exception):
    def __init__(self, en, pt, status='error', candidates=None):
        self.en, self.pt, self.status, self.candidates = en, pt, status, candidates


def envelope(reply, *, status='done', assumptions=None, **extra):
    return dict(ok=status != 'error', reply=reply, status=status,
                assumptions=assumptions or [], **extra)


def _say(lang, en, pt):
    return pt if lang == 'pt' else en


def _setup(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS agent_confirmations (
        code TEXT PRIMARY KEY, actor TEXT NOT NULL, group_id INTEGER NOT NULL,
        expires REAL NOT NULL, snapshot TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS agent_receipts (
        actor TEXT NOT NULL, request_id TEXT NOT NULL, digest TEXT NOT NULL,
        expires REAL NOT NULL, response TEXT NOT NULL, PRIMARY KEY(actor, request_id))''')


def _safe_output(conn, result):
    # Renderers never receive raw identity rows. Defense in depth for a person's
    # display name accidentally configured as a phone or a path in legacy notes.
    numbers = [r[0] for r in conn.execute('SELECT actor FROM group_members')]
    def safe(value):
        if isinstance(value, str):
            for number in numbers:
                if len(number) >= 7:
                    value = value.replace(number, '[member]')
            value = re.sub(r'(?<!\w)\+\d[\d ()-]{6,}\d', '[member]', value)
            return re.sub(r'/(?:home|tmp|var|etc)/[^\s,;]*', '[private path]', value)
        if isinstance(value, list):
            return [safe(v) for v in value]
        if isinstance(value, dict):
            return {k: safe(v) for k, v in value.items()}
        return value
    return safe(result)


def _names(req):
    values = req.get('items', req.get('names', [req['item']] if req.get('item') else []))
    if not isinstance(values, list) or not values or len(values) > 100:
        raise ValueError('items')
    names = [v.get('name') if isinstance(v, dict) else v for v in values]
    if any(not isinstance(n, str) or not n.strip() or len(n) > 500 for n in names):
        raise ValueError('name')
    return list(dict.fromkeys(names))


def _removal_rows(conn, store, req, lang):
    rows = []
    for name in _names(req):
        try:
            row, _ = resolve_item(conn, store, name, unit_filter(req.get('unit')))
        except GroceryError:
            # Removal never selects a partial match without the user's choice.
            exact = conn.execute('SELECT * FROM items WHERE store_id = ? AND '
                                 '(normalized_name = ? OR canonical_name = ?)',
                                 (store['id'], normalized(name), synonyms.canonical(name))).fetchall()
            choices = exact or [r for r, _ in partial_candidates(conn, store, name, unit_filter(req.get('unit')))]
            candidates = [{'name': r['name'], 'unit': r['unit']} for r in choices]
            if not candidates:
                raise _ReplyError(f'I couldn’t find *{name}* on this list. What name is it listed under?',
                                  f'Não encontrei *{name}* nesta lista. Com qual nome ele está anotado?',
                                  'clarification', candidates) from None
            raise _ReplyError('Which of these would you like to remove? Tell me the name and unit, if shown.',
                              'Qual destes você quer tirar? Me diga o nome e a unidade, se aparecer.',
                              'clarification', candidates) from None
        if row['id'] not in [r['id'] for r in rows]:
            rows.append(dict(row))
    return rows


def _dispatch(conn, actor, gid, req, lang, trusted_confirmation, now):
    action = req['action']
    if action in {'help', 'onboard'}:
        person = conn.execute('SELECT lang, default_store FROM people WHERE actor = ?', (actor,)).fetchone() if action == 'onboard' else None
        return envelope(member_render.help_text(lang, onboarding=action == 'onboard', person=dict(person) if person else None))
    if action == 'stores':
        names = [r[0] for r in conn.execute('SELECT name FROM stores WHERE group_id = ? ORDER BY normalized_name', (gid,))]
        return envelope(member_render.store_list(names, lang))
    if action == 'preferences':
        old = conn.execute('SELECT * FROM people WHERE actor = ?', (actor,)).fetchone()
        if not any(key in req for key in ('lang', 'store', 'name', 'timezone', 'walkOrder')):
            person = old or {'lang': lang, 'default_store': '', 'display_name': ''}
            return envelope(member_render.preferences(person, lang, timezone_for(conn, actor)))
        preferred_lang = req.get('lang') or (old['lang'] if old else lang)
        preferred_store = req.get('store')
        # A preference may name a future store; it cannot reach another household.
        name = req.get('name', old['display_name'] if old else '')
        remember_person(conn, actor, preferred_lang, name, preferred_store)
        if req.get('timezone'):
            set_timezone(conn, actor, req['timezone'])
        person = conn.execute('SELECT * FROM people WHERE actor = ?', (actor,)).fetchone()
        reply = member_render.preferences(person, preferred_lang, timezone_for(conn, actor), changed_keys=req.keys())
        if req.get('walkOrder'):
            order = _dispatch(conn, actor, gid, {**req, 'action': 'layout'}, preferred_lang, trusted_confirmation, now)
            return envelope(reply + '\n' + order['reply'], assumptions=order['assumptions'])
        return envelope(reply)
    if action == 'activity':
        params = {k: req[k] for k in ('since', 'until', 'by', 'item', 'store', 'limit') if k in req}
        if 'eventAction' in req or 'changeType' in req:
            params['action'] = req.get('eventAction', req.get('changeType'))
        return envelope(activity.report(conn, actor, gid, lang, **params)['text'])
    if action == 'confirm_remove':
        if not trusted_confirmation:
            raise _ReplyError('Use the confirmation command sent with the preview.', 'Use o comando de confirmação enviado com a prévia.')
        code = req.get('confirmation_code', req.get('code', '')).strip().upper()
        pending = conn.execute('SELECT * FROM agent_confirmations WHERE code = ? AND actor = ? AND group_id = ?', (code, actor, gid)).fetchone()
        if not pending or pending['used'] or pending['expires'] <= now:
            raise _ReplyError('That confirmation is unavailable or expired. Request removal again.', 'Essa confirmação não está disponível ou expirou. Peça a remoção novamente.')
        snapshot = json.loads(pending['snapshot'])
        version = conn.execute('SELECT COALESCE(MAX(id), 0) FROM events WHERE group_id = ? AND store = ?', (gid, snapshot['store'])).fetchone()[0]
        if version != snapshot['version']:
            raise _ReplyError('The list changed. Request removal again for a new preview.', 'A lista mudou. Peça a remoção novamente para obter uma nova prévia.')
        for expected in snapshot['rows']:
            row = conn.execute('SELECT i.* FROM items i JOIN stores s ON s.id = i.store_id WHERE i.id = ? AND s.group_id = ?', (expected['id'], gid)).fetchone()
            if row is None or dict(row) != expected:
                raise _ReplyError('The list changed. Request removal again for a new preview.', 'A lista mudou. Peça a remoção novamente para obter uma nova prévia.')
        removed = []
        for row in snapshot['rows']:
            removed.extend(remove_items(conn, snapshot['store'], [row['name']], row['unit'], actor, gid))
        conn.execute('UPDATE agent_confirmations SET used = 1 WHERE code = ?', (code,))
        return envelope(member_render.changed('removed', removed, snapshot['store'], lang))

    try:
        store_name, assumed = resolve_store(conn, req.get('store'), gid, actor)
    except GroceryError:
        raise _ReplyError('Which store should I use?', 'Qual loja devo usar?', 'clarification') from None
    notes = render_assumptions([assumed] if assumed else [], lang)
    if action == 'add':
        _names(req)
        values = req.get('items', req.get('names', [req['item']] if req.get('item') else []))
        parsed = parse_items_json(json.dumps(values))
        result = ingest_items(conn, store_name, parsed, req.get('sourceType', 'text'),
                              req.get('sourceRef', ''), req.get('rawText', ''),
                              actor=actor, group_id=gid)
        reply = '\n\n'.join(member_render.changed(key, result[field], result['store'], lang)
                         for key, field in [('added', 'added'), ('updated', 'merged')] if result[field])
        return envelope(reply, assumptions=notes)
    store = store_row(conn, store_name, group_id=gid)
    if action == 'list':
        store, groups = grouped_items(conn, store_name, False, gid)
        bought = [] if req.get('neededOnly', req.get('needed_only', False)) else [r for r in current_items(conn, store_name, True, gid)[1] if r['status'] == 'purchased']
        return envelope(member_render.shopping_list(store_name, groups, bought, lang), assumptions=notes)
    if action in {'buy', 'unbuy'}:
        result = set_status(conn, store_name, _names(req), 'purchased' if action == 'buy' else 'needed',
                            unit_filter(req.get('unit')), actor, gid,
                            source_type=req.get('sourceType', ''), source_ref=req.get('sourceRef', ''), raw_text=req.get('rawText', ''))
        notes += render_assumptions(result['assumptions'], lang)
        remaining = len(current_items(conn, store_name, False, gid)[1])
        parts = [member_render.changed('purchased' if action == 'buy' else 'needed', result['items'], store_name, lang)]
        matching = member_render.item_assumptions(result['assumptions'], lang)
        if matching:
            parts.append(matching)
        if result['ambiguous']:
            parts.append(member_render.choices(result['ambiguous'], lang))
            return envelope('\n\n'.join(part for part in parts if part), status='clarification', assumptions=notes, candidates=result['ambiguous'])
        if action == 'buy':
            parts.append(member_render.remaining(remaining, lang))
        return envelope('\n\n'.join(part for part in parts if part), assumptions=notes)
    if action == 'remove':
        rows = _removal_rows(conn, store, req, lang)
        code = secrets.token_hex(4).upper()
        version = conn.execute('SELECT COALESCE(MAX(id), 0) FROM events WHERE group_id = ? AND store = ?', (gid, store_name)).fetchone()[0]
        snapshot = json.dumps({'store': store_name, 'rows': rows, 'version': version}, sort_keys=True)
        # One active preview per requester prevents an old 'yes' targeting a new task.
        conn.execute('DELETE FROM agent_confirmations WHERE actor = ?', (actor,))
        conn.execute('INSERT INTO agent_confirmations(code, actor, group_id, expires, snapshot) VALUES (?, ?, ?, ?, ?)',
                     (code, actor, gid, now + CONFIRMATION_SECONDS, snapshot))
        reply = member_render.removal_preview(rows, store_name, code, lang)
        return envelope(reply, status='confirmation', assumptions=notes, confirmation_code=code)
    if action == 'close':
        result = close_trip(conn, store_name, actor=actor, group_id=gid)
        reply = member_render.trip_closed(store_name, len(result['purchased']), len(result['carried_forward']), result['duplicate'], lang)
        return envelope(reply, assumptions=notes)
    if action == 'reopen':
        result = reopen_trip(conn, store_name, req.get('tripId', req.get('trip_id')), actor, gid)
        return envelope(member_render.trip_reopened(store_name, result['restored'], lang), assumptions=notes)
    if action == 'due':
        rows = due(conn, store_name, req.get('section'), slack=req.get('slack', 1.0), group_id=gid)
        return envelope(member_render.due_items(store_name, rows, lang, req.get('section')), assumptions=notes)
    if action == 'layout':
        layout = req.get('walkOrder', req.get('layout'))
        if layout:
            if layout not in sections.LAYOUTS:
                raise ValueError('layout')
            conn.execute('UPDATE stores SET layout = ? WHERE id = ?', (layout, store['id']))
        layout = layout or store['layout']
        order = member_render.bullets([sections.label(s, lang) for s in sections.layout_order(layout)], '🔹')
        intro = _say(lang, f'Here’s the aisle order for *{store_name}*:', f'A ordem dos corredores de *{store_name}* está assim:')
        return envelope(intro + '\n' + order, assumptions=notes)
    if action == 'history':
        limit = max(1, min(int(req.get('limit', 10)), 50))
        trips = conn.execute('SELECT id, closed_at, closed_by FROM trips WHERE store_id = ? ORDER BY closed_at DESC LIMIT ?', (store['id'], limit)).fetchall()
        lines = []
        zone = ZoneInfo(timezone_for(conn, actor))
        for trip in trips:
            rows = conn.execute('SELECT name, quantity, unit, note, product_url, outcome FROM trip_items WHERE trip_id = ? ORDER BY outcome, name', (trip['id'],)).fetchall()
            local = parse_timestamp(trip['closed_at']).astimezone(zone)
            stamp = local.strftime('%d/%m/%Y %H:%M %Z' if lang == 'pt' else '%Y-%m-%d %H:%M %Z')
            closer = public_label(conn, trip['closed_by'])
            attribution = _say(lang, f' — closed by {closer}', f' — fechada por {closer}') if closer else ''
            lines.append(f"{store_name} · {stamp}{attribution}")
            for row in rows:
                outcome = _say(lang, 'bought', 'comprado') if row['outcome'] == 'purchased' else _say(lang, 'carried over', 'pendente')
                link = f" — {row['product_url']}" if row['product_url'] else ''
                emoji = '✅' if row['outcome'] == 'purchased' else '🛒'
                lines.append(f"{emoji} {item_line(row)} ({outcome}){link}")
        return envelope('\n'.join(lines) or _say(lang, 'No previous trips.', 'Nenhuma compra anterior.'), assumptions=notes)
    raise ValueError('action')


def handle(db_path, actor: str, request: dict[str, Any], *, trusted_confirmation=False, private=False, now=None):
    """Execute once. DB, actor and native authority MUST NOT originate in model args.

    Request IDs are trusted message/call identifiers; same ID + different payload
    fails closed. All domain writes and receipt storage share one SQLite commit.
    """
    conn = None
    lang = 'pt' if isinstance(request, dict) and request.get('lang') == 'pt' else 'en'
    now = time.time() if now is None else now
    try:
        if not isinstance(request, dict) or request.get('action') not in ACTIONS:
            raise ValueError('action')
        if 'lang' in request and request['lang'] not in {'en', 'pt'}:
            raise ValueError('lang')
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError('actor')
        actor = clean_text(actor)
        conn = connect(db_path)
        conn.execute('BEGIN IMMEDIATE')
        tx = _Transaction(conn)
        _setup(tx)
        if private:
            # Only the trusted bridge can select the actor-hashed private DB.
            # Bind it permanently so a mistaken later path cannot cross users.
            conn.execute('CREATE TABLE IF NOT EXISTS agent_private_owner (singleton INTEGER PRIMARY KEY CHECK(singleton = 1), actor TEXT NOT NULL)')
            owner = conn.execute('SELECT actor FROM agent_private_owner WHERE singleton = 1').fetchone()
            if (owner and owner['actor'] != actor) or conn.execute('SELECT 1 FROM group_members WHERE actor != ?', (actor,)).fetchone():
                raise _ReplyError('You do not have access to this grocery list.', 'Você não tem acesso a esta lista de compras.')
            conn.execute('INSERT OR IGNORE INTO agent_private_owner VALUES (1, ?)', (actor,))
            default = group_row(tx)
            conn.execute("INSERT OR IGNORE INTO group_members(group_id, actor, role, added_at) VALUES (?, ?, 'owner', datetime('now'))", (default['id'], actor))
        else:
            private_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'agent_private_owner'").fetchone()
            if private_table and conn.execute('SELECT 1 FROM agent_private_owner WHERE actor != ?', (actor,)).fetchone():
                raise _ReplyError('You do not have access to this grocery list.', 'Você não tem acesso a esta lista de compras.')
        # Unlike the operator CLI, chat never receives a bootstrap-open household.
        if not conn.execute('SELECT 1 FROM group_members WHERE actor = ?', (actor,)).fetchone():
            raise _ReplyError('You do not have access to this grocery list.', 'Você não tem acesso a esta lista de compras.')
        gid = group_for_actor(tx, actor)['id']
        lang = language_for(tx, actor, request.get('lang'))
        if request['action'] == 'confirm_remove' and not trusted_confirmation:
            raise _ReplyError('Use the confirmation command sent with the preview.', 'Use o comando de confirmação enviado com a prévia.')
        request_id = request.get('request_id')
        digest = hashlib.sha256(json.dumps({'group_id': gid, 'request': {k: v for k, v in request.items() if k != 'request_id'}}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if request_id is not None:
            if not isinstance(request_id, str) or not request_id or len(request_id) > 256:
                raise ValueError('request_id')
            receipt = conn.execute('SELECT * FROM agent_receipts WHERE actor = ? AND request_id = ? AND expires > ?', (actor, request_id, now)).fetchone()
            if receipt:
                if receipt['digest'] != digest:
                    raise _ReplyError('That request changed during a retry. Please send it again.', 'Essa solicitação mudou durante uma nova tentativa. Envie novamente.')
                return json.loads(receipt['response'])
        result = _dispatch(tx, actor, gid, request, lang, trusted_confirmation, now)
        # Replies render consequential item choices themselves. Store fallback
        # details remain metadata: naming the store is enough in conversation.
        result = _safe_output(tx, result)
        conn.execute('DELETE FROM agent_confirmations WHERE expires <= ?', (now,))
        conn.execute('DELETE FROM agent_receipts WHERE expires <= ?', (now,))
        if request_id:
            conn.execute('INSERT OR REPLACE INTO agent_receipts VALUES (?, ?, ?, ?, ?)',
                         (actor, request_id, digest, now + RECEIPT_SECONDS, json.dumps(result, ensure_ascii=False)))
        conn.commit()
        return result
    except _ReplyError as exc:
        result = envelope(_say(lang, exc.en, exc.pt), status=exc.status)
        if exc.candidates is not None:
            result['candidates'] = exc.candidates
            # Every member-facing choice belongs in the ready-to-send reply.
            # Models must not reconstruct this list from structured metadata.
            for candidate in exc.candidates:
                label = candidate['name']
                if candidate.get('unit'):
                    label += ' (' + candidate['unit'] + ')'
                result['reply'] += '\n🔹 ' + label
        return _safe_output(conn, result) if conn else result
    except (GroceryError, ValueError, TypeError, KeyError, AttributeError):
        return envelope(_say(lang, 'I could not complete that request. Check the store, item names, and options.',
                             'Não consegui concluir o pedido. Confira a loja, os nomes dos itens e as opções.'), status='error')
    except Exception:
        # Deliberately no raw subprocess/SQLite/path detail in the member channel.
        return envelope(_say(lang, 'The grocery list is temporarily unavailable. Please try again.',
                             'A lista de compras está indisponível no momento. Tente novamente.'), status='error')
    finally:
        if conn is not None:
            conn.rollback()
            conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--actor', required=True)
    parser.add_argument('--request-json', required=True)
    parser.add_argument('--private', action='store_true', help='trusted actor-hashed private DB only')
    parser.add_argument('--trusted-confirmation', action='store_true', help='native authenticated command bridge only')
    args = parser.parse_args()
    try:
        request = json.loads(args.request_json)
    except (ValueError, TypeError):
        request = None
    print(json.dumps(handle(args.db, args.actor, request, trusted_confirmation=args.trusted_confirmation, private=args.private), ensure_ascii=False))


if __name__ == '__main__':
    main()

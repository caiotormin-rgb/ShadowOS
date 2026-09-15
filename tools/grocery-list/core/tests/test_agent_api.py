"""Synthetic integration tests for the member API, never a live database."""
import json
from concurrent.futures import ThreadPoolExecutor
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_api
from db import connect
from groups import add_member
from people import remember_person

ALICE = '+15555550101'
BOB = '+15555550102'
CAROL = '+15555550103'


class AgentApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'shared.sqlite'
        with connect(self.db) as conn:
            add_member(conn, 'A', ALICE)
            add_member(conn, 'A', BOB)
            add_member(conn, 'B', CAROL)
            remember_person(conn, ALICE, 'en', 'Alice')
            remember_person(conn, BOB, 'pt', 'Bob')
        self.add('Milk')

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, action, actor=ALICE, **kw):
        return agent_api.handle(self.db, actor, {'action': action, 'store': 'Shop', **kw})

    def add(self, *names):
        result = self.call('add', items=list(names))
        self.assertTrue(result['ok'], result)
        return result

    def count(self, table):
        with connect(self.db) as conn:
            return conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]

    def test_envelope_is_rendered_and_household_scoped(self):
        self.call('add', actor=CAROL, items=['Secret carrots'])
        for actor in (ALICE, BOB):
            result = self.call('list', actor=actor)
            self.assertEqual(set(result), {'ok', 'reply', 'status', 'assumptions'})
            self.assertIn('Milk', result['reply'])
            self.assertNotIn('Secret', result['reply'])
            self.assertNotIn(ALICE, json.dumps(result))
            self.assertNotIn('store_id', json.dumps(result))
        self.assertIn('Secret carrots', self.call('list', actor=CAROL)['reply'])
        self.assertEqual('error', self.call('list', actor='+15555550199')['status'])

    def test_unconfigured_shared_fails_closed(self):
        result = agent_api.handle(Path(self.tmp.name) / 'empty.sqlite', ALICE, {'action': 'help'})
        self.assertEqual('error', result['status'])

    def test_private_is_persistently_actor_bound(self):
        private_db = Path(self.tmp.name) / 'private.sqlite'
        request = {'action': 'add', 'store': 'Shop', 'items': ['Private apples']}
        self.assertTrue(agent_api.handle(private_db, ALICE, request, private=True)['ok'])
        for private in (True, False):
            result = agent_api.handle(private_db, BOB, {'action': 'list', 'store': 'Shop'}, private=private)
            self.assertEqual('error', result['status'])
            self.assertNotIn('Private apples', result['reply'])
        self.assertNotIn('Private apples', self.call('list')['reply'])

    def test_removal_needs_native_confirmation_actor_and_single_use(self):
        preview = self.call('remove', items=[{'name': 'Milk'}])
        self.assertEqual('confirmation', preview['status'])
        self.assertEqual(1, self.count('items'))
        req = {'action': 'confirm_remove', 'confirmation_code': preview['confirmation_code']}
        self.assertEqual('error', agent_api.handle(self.db, ALICE, req)['status'])
        self.assertEqual('error', agent_api.handle(self.db, BOB, req, trusted_confirmation=True)['status'])
        self.assertEqual(1, self.count('items'))
        self.assertEqual('done', agent_api.handle(self.db, ALICE, req, trusted_confirmation=True)['status'])
        self.assertEqual(0, self.count('items'))
        self.assertEqual('error', agent_api.handle(self.db, ALICE, req, trusted_confirmation=True)['status'])

    def test_expired_and_changed_snapshot_rejected(self):
        req = {'action': 'remove', 'store': 'Shop', 'items': ['Milk']}
        preview = agent_api.handle(self.db, ALICE, req, now=100)
        confirm = {'action': 'confirm_remove', 'code': preview['confirmation_code']}
        self.assertEqual('error', agent_api.handle(self.db, ALICE, confirm, trusted_confirmation=True, now=401)['status'])
        preview = self.call('remove', items=['Milk'])
        self.call('buy', items=['Milk'])
        result = agent_api.handle(self.db, ALICE, {'action': 'confirm_remove', 'code': preview['confirmation_code']}, trusted_confirmation=True)
        self.assertEqual('error', result['status'])
        self.assertIn('changed', result['reply'])
        self.assertEqual(1, self.count('items'))

    def test_remove_ambiguity_has_choices_without_rows(self):
        self.add('Paper towels (Bounty)', 'Paper towels (Generic)')
        result = self.call('remove', items=['paper towels'])
        self.assertEqual('clarification', result['status'])
        self.assertEqual(2, len(result['candidates']))
        self.assertIn('Paper towels (Bounty)', result['reply'])
        self.assertIn('Paper towels (Generic)', result['reply'])
        self.assertEqual({'name', 'unit'}, set(result['candidates'][0]))
        self.assertEqual(3, self.count('items'))

    def test_buy_ambiguity_does_not_invent_item(self):
        self.add('Paper towels (Bounty)', 'Paper towels (Generic)')
        result = self.call('buy', items=['paper towels'])
        self.assertEqual('clarification', result['status'])
        self.assertEqual(3, self.count('items'))
        self.assertIn('Which one', result['reply'])
        self.assertIn('haven’t marked', result['reply'])

    def test_preferences_update_only_requester_preserve_name(self):
        result = self.call('preferences', lang='pt', timezone='America/Sao_Paulo')
        self.assertIn('preferências', result['reply'])
        self.assertIn('💬 Vou responder em português.', result['reply'])
        self.assertIn('🛒 Sua loja de costume: Shop.', result['reply'])
        self.assertNotIn('pt;', result['reply'])
        with connect(self.db) as conn:
            alice = conn.execute('SELECT * FROM people WHERE actor = ?', (ALICE,)).fetchone()
            bob = conn.execute('SELECT * FROM people WHERE actor = ?', (BOB,)).fetchone()
            self.assertEqual('Alice', alice['display_name'])
            self.assertEqual('Shop', alice['default_store'])
            self.assertEqual('', bob['default_store'])
        result = agent_api.handle(self.db, ALICE, {'action': 'list'})
        self.assertTrue(result['ok'])
        self.assertTrue(result['assumptions'])

    def test_retry_receipt_prevents_double_event_and_changed_payload(self):
        first = self.call('buy', items=['Milk'], request_id='message-1/call-1')
        events = self.count('events')
        second = self.call('buy', items=['Milk'], request_id='message-1/call-1')
        self.assertEqual(first, second)
        self.assertEqual(events, self.count('events'))
        self.assertEqual('error', self.call('buy', items=['Eggs'], request_id='message-1/call-1')['status'])
        self.assertEqual(1, self.count('items'))

    def test_receipt_failure_rolls_back_domain_write(self):
        with patch.object(agent_api, '_safe_output', side_effect=RuntimeError('internal /tmp/secret.sqlite')):
            result = self.call('buy', items=['Milk'], request_id='failed')
        self.assertEqual('error', result['status'])
        self.assertNotIn('/tmp', result['reply'])
        with connect(self.db) as conn:
            self.assertEqual('needed', conn.execute('SELECT status FROM items').fetchone()[0])
        self.assertEqual(1, self.count('events'))

    def test_all_common_actions_return_compact_envelopes(self):
        for action in ['list', 'history', 'stores', 'due', 'layout', 'help', 'onboard', 'activity']:
            with self.subTest(action=action):
                result = self.call(action)
                self.assertTrue(result['ok'], result)
                self.assertIsInstance(result['reply'], str)
        self.assertTrue(self.call('buy', items=['Milk'])['ok'])
        self.assertTrue(self.call('unbuy', items=['Milk'])['ok'])
        self.assertTrue(self.call('buy', items=['Milk'])['ok'])
        self.assertTrue(self.call('close')['ok'])
        self.assertTrue(self.call('history')['ok'])
        self.assertTrue(self.call('reopen')['ok'])

    def test_parallel_same_request_is_one_mutation(self):
        request = {'action': 'buy', 'store': 'Shop', 'items': ['Milk'], 'request_id': 'parallel-call'}
        before = self.count('events')
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: agent_api.handle(self.db, ALICE, request), range(4)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertTrue(results[0]['ok'])
        self.assertEqual(before + 1, self.count('events'))

    def test_receipt_cannot_replay_after_household_change(self):
        self.call('list', request_id='list-call')
        with connect(self.db) as conn:
            conn.execute('DELETE FROM group_members WHERE actor = ?', (ALICE,))
            conn.commit()
            add_member(conn, 'B', ALICE)
        result = self.call('list', request_id='list-call')
        self.assertEqual('error', result['status'])
        self.assertNotIn('Milk', result['reply'])

    def test_event_version_catches_edit_reverted_to_same_row(self):
        preview = self.call('remove', items=['Milk'])
        with connect(self.db) as conn:
            old = dict(conn.execute('SELECT * FROM items').fetchone())
        self.call('buy', items=['Milk'])
        with connect(self.db) as conn:
            conn.execute('UPDATE items SET status = ?, updated_at = ? WHERE id = ?',
                         (old['status'], old['updated_at'], old['id']))
            conn.commit()
        result = agent_api.handle(self.db, ALICE, {'action': 'confirm_remove', 'code': preview['confirmation_code']}, trusted_confirmation=True)
        self.assertEqual('error', result['status'])
        self.assertEqual(1, self.count('items'))

    def test_store_fallback_metadata_does_not_repeat_in_conversation(self):
        for action, fields in [('list', {}), ('add', {'items': ['Eggs']}), ('buy', {'items': ['Milk']})]:
            result = agent_api.handle(self.db, ALICE, {'action': action, **fields})
            self.assertTrue(result['assumptions'])
            for note in result['assumptions']:
                self.assertNotIn(note, result['reply'])
            self.assertIn('Shop', result['reply'])

    def test_history_preserves_member_visible_details(self):
        self.call('add', items=[{'name': 'Fancy coffee', 'quantity': 2, 'unit': 'bags', 'note': 'decaf', 'productUrl': 'https://example.com/coffee'}])
        self.call('buy', items=['Fancy coffee'])
        self.call('close')
        with connect(self.db) as conn:
            conn.execute("UPDATE trips SET closed_at = '2099-01-01T12:00:00+00:00'")
            conn.commit()
        result = self.call('history')
        for expected in ['closed by Alice', '2099-01-01 07:00 EST', 'decaf', 'https://example.com/coffee', 'x2 bags', 'carried over']:
            self.assertIn(expected, result['reply'])
        self.assertNotIn(ALICE, result['reply'])

    def test_preferences_layout_updates_and_validates(self):
        result = self.call('preferences', walkOrder='warehouse')
        self.assertTrue(result['ok'], result)
        with connect(self.db) as conn:
            self.assertEqual('warehouse', conn.execute('SELECT layout FROM stores').fetchone()[0])
        result = self.call('preferences', walkOrder='unknown')
        self.assertEqual('error', result['status'])
        with connect(self.db) as conn:
            self.assertEqual('warehouse', conn.execute('SELECT layout FROM stores').fetchone()[0])

    def test_other_household_activity_cannot_steer_default_store(self):
        # Both households have Shop; Alice then uses a different local store.
        # Carol's later Shop event must not become Alice's fallback choice.
        result = agent_api.handle(self.db, ALICE, {'action': 'add', 'store': 'Market', 'items': ['Local pears']})
        self.assertTrue(result['ok'])
        self.call('add', actor=CAROL, items=['Secret carrots'])
        result = agent_api.handle(self.db, ALICE, {'action': 'list'})
        self.assertIn('Market', result['reply'])
        self.assertIn('Local pears', result['reply'])
        self.assertNotIn('Milk', result['reply'])
        self.assertNotIn('Secret carrots', result['reply'])

    def test_activity_change_type_matches_bridge_field(self):
        self.call('buy', items=['Milk'])
        result = self.call('activity', changeType='added')
        self.assertTrue(result['ok'], result)
        self.assertNotIn('bought', result['reply'].lower())
        self.assertIn('added', result['reply'].lower())

    def test_preferences_without_fields_does_not_create_person(self):
        result = agent_api.handle(self.db, CAROL, {'action': 'preferences'})
        self.assertTrue(result['ok'])
        self.assertNotIn('saved', result['reply'])
        with connect(self.db) as conn:
            self.assertIsNone(conn.execute('SELECT * FROM people WHERE actor = ?', (CAROL,)).fetchone())

    def test_short_portuguese_batch_names_items_with_emoji_bullets(self):
        result = self.call('add', actor=BOB, items=['Arroz', 'Feijão'])
        self.assertTrue(result['ok'])
        self.assertIn('Pronto, anotei', result['reply'])
        self.assertIn('🛒 Arroz', result['reply'])
        self.assertIn('🛒 Feijão', result['reply'])
        self.assertLess(len(result['reply']), 180)
        result = self.call('list', actor=BOB)
        self.assertIn('Aqui está sua lista', result['reply'])
        self.assertIn('🥫 Arroz', result['reply'])
        self.assertIn('🥛 Milk', result['reply'])
        self.assertNotIn('última loja mexida', result['reply'])

    def test_long_confirmation_is_brief_but_explicit_list_keeps_every_item(self):
        names = [f'Product {i}' for i in range(9)]
        result = self.call('add', items=names)
        self.assertIn('9 items', result['reply'])
        self.assertLess(len(result['reply']), 100)
        result = self.call('list')
        for name in names:
            self.assertIn(name, result['reply'])

    def test_list_keeps_quantities_notes_and_purchased_section(self):
        self.call('add', items=[{'name': 'Rice', 'quantity': 2, 'unit': 'kg', 'note': 'brown'}])
        self.call('buy', items=['Milk'])
        result = self.call('list')
        self.assertIn('🥫 Rice — 2 kg (brown)', result['reply'])
        self.assertIn('Already bought:', result['reply'])
        self.assertIn('✅ Milk', result['reply'])
        needed = self.call('list', neededOnly=True)
        self.assertNotIn('✅ Milk', needed['reply'])

    def test_partial_success_reply_preserves_unresolved_choices(self):
        self.add('Paper towels (Bounty)', 'Paper towels (Generic)')
        result = self.call('buy', items=['Milk', 'paper towels'])
        self.assertEqual('clarification', result['status'])
        self.assertIn('✅ Milk', result['reply'])
        self.assertIn('🔹 Paper towels (Bounty)', result['reply'])
        self.assertIn('🔹 Paper towels (Generic)', result['reply'])
        self.assertIn('haven’t marked it yet', result['reply'])
        self.assertNotIn('everything on the list', result['reply'])
        with connect(self.db) as conn:
            states = {row['name']: row['status'] for row in conn.execute('SELECT name,status FROM items')}
            self.assertEqual('purchased', states['Milk'])
            self.assertEqual('needed', states['Paper towels (Bounty)'])

    def test_consequential_partial_match_still_explained_naturally(self):
        self.add('Paper towels (Bounty)')
        result = self.call('buy', actor=BOB, items=['Bounty'])
        self.assertEqual('done', result['status'])
        self.assertIn('Entendi *Bounty* como *Paper towels (Bounty)*.', result['reply'])
        self.assertIn('✅ Paper towels (Bounty)', result['reply'])
        self.assertTrue(result['assumptions'])

    def test_onboarding_only_asks_for_missing_preferences(self):
        self.call('preferences', lang='pt')
        result = self.call('onboard')
        self.assertIn('*Shop*', result['reply'])
        self.assertIn('responder em português', result['reply'])
        self.assertNotIn('?', result['reply'])
        result = self.call('onboard', actor=BOB)
        self.assertIn('Qual loja', result['reply'])
        self.assertNotIn('prefere conversar', result['reply'])
        result = self.call('onboard', actor=CAROL)
        self.assertIn('Which store', result['reply'])
        self.assertIn('English or Portuguese?', result['reply'])

    def test_errors_do_not_echo_model_internals(self):
        for request in [None, {'action': 'nope'}, {'action': 'add', 'store': '/tmp/private.sqlite', 'items': [{'name': 'milk', 'quantity': 'bad'}]}]:
            result = agent_api.handle(self.db, ALICE, request)
            self.assertEqual('error', result['status'])
            self.assertNotIn('/tmp/', result['reply'])
            self.assertNotIn('sqlite', result['reply'])


if __name__ == '__main__':
    unittest.main()

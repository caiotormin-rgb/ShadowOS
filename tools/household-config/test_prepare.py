"""Synthetic rollout preparation checks; never reads real config or state."""
import copy
import hashlib
import json
import stat
import tempfile
import unittest
from pathlib import Path

import prepare


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state'
        self.workspace = self.root / 'source'
        self.config = self.root / 'baseline.json'
        self.output = self.root / 'bundle'
        self.baseline = {
            'models': {'providers': {'custom': {'apiKey': 'synthetic-secret', 'models': ['unchanged']}}},
            'agents': {'defaults': {'models': {prepare.SOL: {'agentRuntime': {'id': 'openclaw'}}, 'other': {}}},
                       'ownership': 'explicit', 'entries': {
                           'main': {'model': {'primary': 'other', 'fallbacks': ['backup']}, 'tools': {'profile': 'full'}, 'custom': True},
                           'shared-tools': {'workspace': str(self.state / 'existing-household'), 'model': {'primary': 'previous', 'fallbacks': ['backup']},
                                            'models': {'previous': {}}, 'tools': {'profile': 'full', 'allow': ['grocery_list', 'doctor_search'], 'deny': ['exec']},
                                            'memory': {'search': {'enabled': False}}},
                           'other-agent': {'model': 'untouched'},
                       }},
            'bindings': [{'agentId': 'main', 'match': {'channel': 'owner'}}, {'agentId': 'shared-tools', 'match': {'channel': 'whatsapp', 'accountId': 'family'}}],
            'channels': {'whatsapp': {'allowFrom': ['+15555550101'], 'accounts': {'family': {'allowFrom': ['+15555550102']}}}},
            'plugins': {'allow': ['whatsapp', 'access', 'grocery-list-tool', 'doctor-search-tool'], 'load': {'paths': ['/existing/plugin']},
                        'entries': {
                            'grocery-list-tool': {'enabled': True, 'config': {'allowedRequesters': ['+15555550101', '+15555550102'], 'whatsappAccountId': 'family', 'familyDbPath': '/unchanged/grocery.sqlite'}},
                            'doctor-search-tool': {'enabled': True, 'config': {'dbPath': '/unchanged/doctor.sqlite'}},
                            'access': {'enabled': True, 'config': {'dbPath': '/custom/access.sqlite', 'enforceAgents': ['existing-agent']}},
                            'other': {'enabled': True, 'config': {'apiKey': 'another-synthetic-secret'}},
                        }},
            'unknownSetting': {'keep': [1, 2, 3]},
        }
        self.config.write_text(json.dumps(self.baseline))

    def tearDown(self):
        self.tmp.cleanup()

    def build(self):
        return prepare.prepare(self.config, self.output, self.workspace, self.state)

    def test_candidate_preserves_models_bindings_allowlists_and_unrelated_config(self):
        before = self.config.read_bytes()
        manifest = self.build()
        candidate = json.loads((self.output / 'config.candidate.json').read_text())
        for field in ['models', 'bindings', 'channels', 'unknownSetting']:
            self.assertEqual(self.baseline[field], candidate[field])
        for agent in ['main', 'other-agent']:
            self.assertEqual(self.baseline['agents']['entries'][agent], candidate['agents']['entries'][agent])
        self.assertEqual(self.baseline['agents']['defaults'], candidate['agents']['defaults'])
        self.assertEqual(self.baseline['agents']['entries']['shared-tools']['models'], candidate['agents']['entries']['shared-tools']['models'])
        self.assertEqual(before, self.config.read_bytes())
        self.assertEqual(hashlib.sha256(before).hexdigest(), manifest['baseline']['sha256'])
        self.assertFalse(self.state.exists())
        self.assertFalse(self.workspace.exists())

    def test_router_and_access_are_explicit_append_only(self):
        self.build()
        candidate = json.loads((self.output / 'config.candidate.json').read_text())
        plugins = candidate['plugins']
        self.assertEqual(self.baseline['plugins']['allow'] + ['household-router'], plugins['allow'])
        self.assertEqual(['/existing/plugin', str(self.workspace / 'tools/household-router')], plugins['load']['paths'])
        self.assertEqual(['existing-agent', 'shared-tools'], plugins['entries']['access']['config']['enforceAgents'])
        for plugin in ['grocery-list-tool', 'doctor-search-tool']:
            self.assertEqual('/custom/access.sqlite', plugins['entries'][plugin]['config']['accessDbPath'])
        self.assertFalse(plugins['entries']['household-router']['enabled'])
        self.assertNotIn('hooks', plugins['entries']['household-router'])
        router = plugins['entries']['household-router']['config']
        self.assertEqual(str(self.state / 'household-router/modes.sqlite3'), router['statePath'])
        self.assertEqual('groceries', router['defaultMode'])
        self.assertNotIn('models', router)
        self.assertEqual(['family'], router['accountIds'])
        self.assertEqual(self.baseline['plugins']['entries']['grocery-list-tool']['config']['allowedRequesters'], router['allowedSenders'])
        shared = candidate['agents']['entries']['shared-tools']
        self.assertEqual([*prepare.GROCERY_TOOLS, 'doctor_search'], shared['tools']['allow'])
        self.assertEqual(['exec'], shared['tools']['deny'])
        self.assertEqual({'primary': prepare.SOL, 'fallbacks': []}, shared['model'])

    def test_dev_is_separate_unbound_sol_only_and_memory_disabled(self):
        self.build()
        candidate = json.loads((self.output / 'config.candidate.json').read_text())
        dev = candidate['agents']['entries']['shadow-dev']
        self.assertEqual(str(self.state / 'workspace-shadow-dev'), dev['workspace'])
        self.assertEqual({'primary': prepare.SOL, 'fallbacks': []}, dev['model'])
        self.assertEqual([prepare.SOL], list(dev['models']))
        self.assertEqual({'profile': 'coding'}, dev['tools'])
        self.assertFalse(dev['memory']['search']['enabled'])
        self.assertFalse(any(b.get('agentId') == 'shadow-dev' for b in candidate['bindings']))

    def test_every_output_is_private_and_rendered_hashes_match(self):
        manifest = self.build()
        for path in [self.output, *self.output.rglob('*')]:
            self.assertEqual(0o700 if path.is_dir() else 0o600, stat.S_IMODE(path.stat().st_mode), path.name)
        for artifact in manifest['files']:
            data = (self.output / artifact['source']).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), artifact['sha256'])
        combined = (self.output / 'workspaces/shared-tools/AGENTS.md').read_text()
        self.assertIn('grocery_show', combined)
        self.assertIn('doctor_search', combined)
        self.assertIn('Automatic approval review rejected', manifest['blocked_steps'][0]['reason'])
        self.assertEqual(['shared-tools'], manifest['blocked_steps'][0]['scope']['agents'])
        self.assertIn('NEVER pass', manifest['configuration_artifacts']['config.patch.json'])
        self.assertIn('ShadowDev', (self.output / 'workspaces/shadow-dev/AGENTS.md').read_text())
        self.assertIn('grocery_show', (self.output / 'workspaces/shared-tools/instructions/GROCERIES.md').read_text())
        self.assertIn('doctor', (self.output / 'workspaces/shared-tools/instructions/DOCTOR.md').read_text().lower())

    def test_patch_recreates_candidate_and_does_not_include_unrelated_secrets(self):
        self.build()
        patch = json.loads((self.output / 'config.patch.json').read_text())
        result = copy.deepcopy(self.baseline)
        for op in patch:
            parts = [p.replace('~1', '/').replace('~0', '~') for p in op['path'].strip('/').split('/')]
            target = result
            for key in parts[:-1]:
                target = target[key]
            if op['op'] == 'remove':
                del target[parts[-1]]
            else:
                target[parts[-1]] = op['value']
        self.assertEqual(json.loads((self.output / 'config.candidate.json').read_text()), result)
        self.assertNotIn('synthetic-secret', json.dumps(patch))

    def test_preparing_candidate_again_is_idempotent(self):
        self.build()
        manifest = prepare.prepare(self.output / 'config.candidate.json', self.root / 'again', self.workspace, self.state)
        self.assertEqual([], json.loads((self.root / 'again/config.patch.json').read_text()))
        self.assertEqual('prepared-not-applied', manifest['status'])

    def test_existing_output_or_conflicting_dev_refused_without_overwrite(self):
        self.output.mkdir()
        keep = self.output / 'keep'
        keep.write_text('existing')
        with self.assertRaises(ValueError):
            self.build()
        self.assertEqual('existing', keep.read_text())
        self.baseline['agents']['entries']['shadow-dev'] = {'workspace': '/another/workspace'}
        with self.assertRaises(ValueError):
            prepare.candidate_config(self.baseline, self.workspace, self.state)

    def test_public_dev_binding_and_noncanonical_sender_fail_closed(self):
        altered = copy.deepcopy(self.baseline)
        altered['bindings'].append({'agentId': 'shadow-dev', 'match': {'channel': 'whatsapp'}})
        with self.assertRaises(ValueError):
            prepare.candidate_config(altered, self.workspace, self.state)
        altered = copy.deepcopy(self.baseline)
        altered['plugins']['entries']['grocery-list-tool']['config']['allowedRequesters'] = ['ambiguous']
        with self.assertRaises(ValueError):
            prepare.candidate_config(altered, self.workspace, self.state)


if __name__ == '__main__':
    unittest.main()

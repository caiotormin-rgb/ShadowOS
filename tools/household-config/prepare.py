#!/usr/bin/env python3
"""Prepare a private, reviewable household rollout bundle; never apply it.

The candidate preserves the complete baseline, including unknown settings and
credentials. All generated files therefore use mode 0600 inside 0700 directories.
The RFC 6902 patch and baseline hash are review/compare-before-apply artifacts,
not permission to overwrite a newer configuration.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path

from render import render

SOL = 'openai/gpt-5.6-sol'
GROCERY_TOOLS = ['grocery_show', 'grocery_add', 'grocery_mark', 'grocery_remove',
                 'grocery_trip', 'grocery_activity', 'grocery_preferences']
ROOT = Path(__file__).resolve().parents[2]


def _append(existing, value):
    if not isinstance(existing, list):
        raise ValueError('Expected an existing array; refusing to replace its type.')
    if value not in existing:
        existing.append(value)


def _entry(entries, name):
    value = entries.get(name)
    if not isinstance(value, dict):
        raise ValueError(f'Required configuration entry is missing: {name}')
    return value


def candidate_config(baseline, workspace_root, state_root):
    """Pure preparation. Deliberately supports the deployed agents.entries schema."""
    candidate = copy.deepcopy(baseline)
    workspace_root, state_root = Path(workspace_root).resolve(), Path(state_root).resolve()
    entries = _entry(candidate, 'agents').get('entries')
    if not isinstance(entries, dict):
        raise ValueError('Expected agents.entries mapping; refusing to guess another schema.')
    main = _entry(entries, 'main')
    shared = _entry(entries, 'shared-tools')
    plugins = _entry(candidate, 'plugins')
    plugin_entries = _entry(plugins, 'entries')
    grocery = _entry(_entry(plugin_entries, 'grocery-list-tool'), 'config')
    doctor = _entry(_entry(plugin_entries, 'doctor-search-tool'), 'config')
    access_entry = _entry(plugin_entries, 'access')
    access = access_entry.setdefault('config', {})
    if not isinstance(access, dict):
        raise ValueError('Expected access plugin config object.')
    requesters = grocery.get('allowedRequesters')
    account = grocery.get('whatsappAccountId')
    if not isinstance(requesters, list) or not requesters or not all(isinstance(v, str) and v for v in requesters):
        raise ValueError('Grocery allowedRequesters must already be configured.')
    if not isinstance(account, str) or not account:
        raise ValueError('Grocery whatsappAccountId must already be configured.')
    # Normalize only the new router copy; retain the existing allowlist exactly.
    # The deployed requesters are E.164. Refuse JIDs/ambiguous forms here rather
    # than silently broadening the trusted sender set.
    import re
    if any(not re.fullmatch(r'\+[1-9][0-9]{6,14}', sender) for sender in requesters):
        raise ValueError('Router preparation requires canonical E.164 requesters.')
    shared_workspace = Path(shared.get('workspace', state_root / 'workspace-shared-tools')).expanduser().resolve()
    instructions = shared_workspace / 'instructions'
    access_db = str(Path(access.get('dbPath', state_root / 'access/access.sqlite3')).expanduser().resolve())
    grocery['accessDbPath'] = access_db
    doctor['accessDbPath'] = access_db
    _append(access.setdefault('enforceAgents', []), 'shared-tools')
    access_entry['enabled'] = True
    _append(plugins.setdefault('allow', []), 'household-router')
    _append(plugins.setdefault('load', {}).setdefault('paths', []), str(workspace_root / 'tools/household-router'))
    # Mode hooks await explicit approval; keep static combined instructions
    # and disable the router so missing hook grants cannot deny guest tools.
    router = {
        'enabled': False,
        'config': {
            'statePath': str(state_root / 'household-router/modes.sqlite3'),
            'agentIds': ['shared-tools'],
            'accountIds': [account],
            'allowedSenders': list(dict.fromkeys(requesters)),
            'defaultMode': 'groceries',
            'tools': {'groceries': list(GROCERY_TOOLS), 'doctor': ['doctor_search']},
            'instructionPaths': {'groceries': str(instructions / 'GROCERIES.md'),
                                 'doctor': str(instructions / 'DOCTOR.md')},
        },
    }
    existing_router = plugin_entries.get('household-router')
    if existing_router is not None and existing_router != router:
        raise ValueError('A different household-router config already exists; review it before replacing.')
    plugin_entries['household-router'] = router
    shared.setdefault('tools', {})['allow'] = [*GROCERY_TOOLS, 'doctor_search']
    shared['model'] = {'primary': SOL, 'fallbacks': []}
    shared['workspace'] = str(shared_workspace)
    # A separate model catalog is intentionally small while all pre-existing
    # catalogs and main-agent choices remain byte-for-byte equivalent as data.
    sol_config = copy.deepcopy(main.get('models', {}).get(SOL,
                           candidate['agents'].get('defaults', {}).get('models', {}).get(SOL, {})))
    dev_workspace = state_root / 'workspace-shadow-dev'
    dev = {
        'name': 'ShadowDev', 'workspace': str(dev_workspace),
        'agentDir': str(state_root / 'agents/shadow-dev/agent'),
        'model': {'primary': SOL, 'fallbacks': []},
        'models': {SOL: sol_config}, 'modelPolicy': {'allow': [SOL]},
        'utilityModel': '', 'tools': {'profile': 'coding'},
        'memory': {'search': {'enabled': False}}, 'skills': [],
    }
    if 'shadow-dev' in entries and entries['shadow-dev'] != dev:
        raise ValueError('A different shadow-dev agent already exists; refusing to overwrite it.')
    # An unbound agent is owner-invoked only. Do not reuse an existing public
    # binding or silently rewrite it; root deployment review verifies sessions.
    for binding in candidate.get('bindings', []):
        if isinstance(binding, dict) and binding.get('agentId') == 'shadow-dev':
            raise ValueError('shadow-dev already has a channel binding; refusing owner-only claim.')
    entries['shadow-dev'] = dev
    return candidate, shared_workspace, dev_workspace


def json_patch(before, after, path=''):
    """Minimal structural RFC 6902 patch; array replacements retain prior values."""
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        escape = lambda value: value.replace('~', '~0').replace('/', '~1')
        for key in sorted(before.keys() - after.keys()):
            result.append({'op': 'remove', 'path': path + '/' + escape(key)})
        for key in sorted(after):
            next_path = path + '/' + escape(key)
            if key not in before:
                result.append({'op': 'add', 'path': next_path, 'value': after[key]})
            else:
                result.extend(json_patch(before[key], after[key], next_path))
        return result
    return [{'op': 'replace', 'path': path, 'value': after}]


def _json(value):
    return json.dumps(value, indent=2, ensure_ascii=False) + '\n'


def prepare(config_path, output, workspace_root=None, state_root=None):
    config_path, output = Path(config_path).expanduser().resolve(), Path(output).expanduser().absolute()
    state_root = Path(state_root or Path.home() / '.openclaw').expanduser().resolve()
    workspace_root = Path(workspace_root or state_root / 'workspace').expanduser().resolve()
    if output.is_symlink():
        raise ValueError('Output must not be a symlink.')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('Output must be a new or empty private staging directory.')
    raw = config_path.read_bytes()
    baseline = json.loads(raw)
    candidate, shared_workspace, dev_workspace = candidate_config(baseline, workspace_root, state_root)
    patch = json_patch(baseline, candidate)
    artifacts = {
        'config.candidate.json': _json(candidate),
        'config.patch.json': _json(patch),
        'workspaces/shared-tools/AGENTS.md': render('combined'),
        'workspaces/shared-tools/instructions/GROCERIES.md': render('groceries'),
        'workspaces/shared-tools/instructions/DOCTOR.md': render('doctor'),
        'workspaces/shadow-dev/AGENTS.md': (Path(__file__).resolve().parent / 'prompts/DEV.md').read_text(),
    }
    targets = {
        'config.candidate.json': str(config_path),
        'workspaces/shared-tools/AGENTS.md': str(shared_workspace / 'AGENTS.md'),
        'workspaces/shared-tools/instructions/GROCERIES.md': str(shared_workspace / 'instructions/GROCERIES.md'),
        'workspaces/shared-tools/instructions/DOCTOR.md': str(shared_workspace / 'instructions/DOCTOR.md'),
        'workspaces/shadow-dev/AGENTS.md': str(dev_workspace / 'AGENTS.md'),
    }
    manifest = {
        'format_version': 1, 'status': 'prepared-not-applied',
        'baseline': {'path': str(config_path), 'sha256': hashlib.sha256(raw).hexdigest()},
        'target_workspace_root': str(workspace_root), 'target_state_root': str(state_root),
        'files': [{'source': source, 'target': target, 'mode': '0600',
                   'sha256': hashlib.sha256(artifacts[source].encode()).hexdigest()}
                  for source, target in targets.items()],
        'configuration_artifacts': {
            'config.candidate.json': 'Full config-shaped candidate; only use after baseline comparison and installed-runtime validation.',
            'config.patch.json': 'RFC 6902 review-only diff. NEVER pass this artifact to the OpenClaw config patch CLI.',
        },
        'blocked_steps': [{
            'action': 'Enable explicit household mode routing',
            'reason': 'Automatic approval review rejected the required hook grants; router stays disabled and combined AGENTS is the active fallback.',
            'requires_explicit_approval': 'household-router entry hooks allowConversationAccess=true and allowPromptInjection=true, then router enabled=true.',
            'scope': {'agents': ['shared-tools'], 'allowed_sender_count': len(candidate['plugins']['entries']['household-router']['config']['allowedSenders']),
                      'accounts': candidate['plugins']['entries']['household-router']['config']['accountIds'],
                      'turns': 'Every household turn for these explicitly configured agents, accounts, and senders.'},
            'after_approval': 'Regenerate the approved candidate and use base-only AGENTS with mode-selected instruction files; do not silently modify this bundle.',
        }],
        'required_before_apply': [
            'Compare the current config bytes SHA256 with baseline.sha256; abort on mismatch.',
            'Review config.patch.json and validate config.candidate.json with the installed runtime schema.',
            'Verify existing access grants, source/build deployment, and trusted command registration.',
            'Back up config and each existing target file; preserve bindings and channel allowlists.',
            'Apply only the reviewed candidate and generated files; this program does not apply or restart.',
        ],
    }
    artifacts['manifest.json'] = _json(manifest)
    # Nothing above writes. File creation uses O_EXCL and private permissions,
    # so a pre-existing filename is never overwritten and no live target opens.
    output.mkdir(parents=True, mode=0o700, exist_ok=True)
    output.chmod(0o700)
    for relative, text in artifacts.items():
        destination = output / relative
        destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        for parent in destination.parents:
            if parent == output:
                break
            parent.chmod(0o700)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(text)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--workspace-root', type=Path)
    parser.add_argument('--state-root', type=Path)
    args = parser.parse_args()
    try:
        prepare(args.config, args.output, args.workspace_root, args.state_root)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        # Never print config values, paths from exceptions, or credential text.
        raise SystemExit(f'Bundle preparation failed ({type(exc).__name__}); no deployment was applied.') from None
    print('Private deployment bundle prepared; no live files changed.')


if __name__ == '__main__':
    main()

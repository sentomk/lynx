# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Compare pristine upstream fields with the effective Lynx contract."""
import subprocess
import sys
from cdpc.model import Loader


def update(repo, revision=None):
    script = repo.protocol / 'scripts/update_cdp_upstream.py'
    result = subprocess.run([sys.executable, str(script), '--write', '--revision', revision or 'master'],
                            cwd=repo.root, text=True, capture_output=True)
    if result.returncode:
        raise OSError('upstream update failed: ' + result.stderr.strip())
    return result.stdout.strip()


def field_at(schema, parts):
    for i, part in enumerate(parts):
        prop = (schema or {}).get('properties', {}).get(part)
        if prop is None:
            return None
        if i == len(parts) - 1:
            return prop
        schema = prop['schema']
    return None


def diff(repo, contract, sources, domain=None):
    domains = {item['domain'] for item in contract.upstream_raw.get('domains', [])}
    touched = {name.split('.')[0] for name in [*sources.methods, *sources.events]}
    touched |= {entry.name.split('.')[0] for entry in contract.entries.values()}
    touched |= contract.deviations.keys()
    if domain and domain not in domains | touched:
        raise ValueError(f'unknown domain: {domain}')
    selected = {domain} if domain else touched
    report = {
        'unimplemented_upstream_methods': sorted(
            entry.name for entry in contract.upstream_entries.values()
            if entry.kind == 'method' and entry.name.split('.')[0] in selected
            and entry.name not in sources.methods),
        'undeclared_events': sorted(
            name for name in sources.events if name.split('.')[0] in selected
            and contract.get('event', name) is None),
        'deviations': [],
    }
    # As in the original comparison, do not compare the already-modified
    # upstream object to itself. Build the baseline from the pristine JSON.
    loader = Loader(repo)
    baseline = {}
    for item in contract.upstream_raw.get('domains', []):
        for section, kind in [('commands', 'method'), ('events', 'event')]:
            for entry in item.get(section, []):
                name = item['domain'] + '.' + entry['name']
                baseline[(kind, name)] = {
                    field: loader.fields(entry.get(field, []), item['domain'], field)
                    for field in ('parameters', 'returns')}
    for name in sorted(contract.deviations):
        if name not in selected:
            continue
        for change in contract.deviations[name]:
            if not isinstance(change, dict) or not isinstance(change.get('target'), str):
                continue  # check reports invalid deviation declarations.
            parts = change['target'].split('.')
            if len(parts) < 3 or parts[1] not in ('parameters', 'returns'):
                continue
            entry_name = name + '.' + parts[0]
            candidates = [entry for entry in contract.upstream_entries.values() if entry.name == entry_name]
            if len(candidates) != 1:
                continue
            entry = contract.get(candidates[0].kind, entry_name) or candidates[0]
            before = field_at(baseline[(entry.kind, entry_name)][parts[1]], parts[2:])
            after = field_at(entry.params if parts[1] == 'parameters' else entry.returns, parts[2:])
            report['deviations'].append({
                'entry': entry_name, 'target': '.'.join(parts[1:]),
                'kind': change.get('kind'), 'before': before, 'after': after,
                'reason': change.get('reason', '')})
    return report

# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Create one editable YAML contract, never an implementation or registration."""
from copy import deepcopy
import re
import yaml

from cdpc.model import Diagnostic, entry_key


def create(repo, contract, name, *, event=False, from_upstream=False):
    if not re.fullmatch(r'[A-Za-z_]\w*\.[A-Za-z_]\w*', name):
        raise ValueError('entry name must be Domain.name')
    domain, leaf = name.split('.')
    kind = 'event' if event else 'method'
    path = repo.docs / domain / ('events' if event else 'methods') / (leaf + '.yaml')
    if path.exists():
        return path, [Diagnostic(repo.relative(path), 1, 'E101', 'file already exists; not overwritten', name)]
    source = None
    if from_upstream:
        source = contract.upstream_entries.get(entry_key(kind, name))
        if source is None:
            return path, [Diagnostic(repo.relative(repo.upstream), 1, 'E101',
                                     f'upstream {kind} does not exist: {name}', name)]
    raw = source.document if source else {}
    data = {'summary': raw.get('description', 'TODO: describe this ' + kind),
            'parameters': deepcopy(raw.get('parameters', []))}
    if not event:
        data['returns'] = deepcopy(raw.get('returns', []))
    data['examples'] = [{'title': 'TODO: add a representative exchange',
                         'event' if event else 'request':
                             {'method': name, 'params': {}} if event else
                             {'id': 1, 'method': name, 'params': {}}}]
    data['notes'] = ['TODO: describe Lynx behavior and limitations.']
    # Keep placeholders visible to check, including for entries with no fields.
    if 'TODO' not in data['summary']:
        data['summary'] += '\nTODO: describe Lynx support.'
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('x', encoding='utf-8') as file:
            yaml.safe_dump(data, file, allow_unicode=True, sort_keys=False)
    except FileExistsError:
        return path, [Diagnostic(repo.relative(path), 1, 'E101', 'file already exists; not overwritten', name)]
    return path, []

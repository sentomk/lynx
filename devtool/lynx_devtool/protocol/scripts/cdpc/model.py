# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Load documents and upstream definitions without scanning or writing sources."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
import fnmatch
import json
from pathlib import Path
import re
from typing import Literal

import yaml
from cdpc.schema import KINDS, Schema, kinds


@dataclass(frozen=True)
class Diagnostic:
    file: str
    line: int
    code: str
    message: str
    entry: str = ''
    severity: str = 'error'


@dataclass(frozen=True)
class Repo:
    root: Path

    @classmethod
    def current(cls):
        return cls(Path(__file__).resolve().parents[5])

    @property
    def protocol(self):
        return self.root / 'devtool/lynx_devtool/protocol'

    @property
    def docs(self):
        return self.protocol / 'custom_cdp_docs'

    @property
    def upstream(self):
        return self.protocol / 'cdp_upstream/protocol.json'

    @property
    def support(self):
        return self.protocol / 'cdp_support.yaml'

    @property
    def manifest(self):
        return self.protocol / 'cdp_manifest.generated.yaml'

    def relative(self, path):
        return Path(path).relative_to(self.root).as_posix()


@dataclass(frozen=True)
class Entry:
    name: str
    kind: Literal['method', 'event']
    params: Schema
    returns: Schema | None
    examples: list
    origin: Literal['local', 'upstream', 'both']
    source: Path | None = None
    line_of: dict[str, int] = field(default_factory=dict)
    document: dict = field(default_factory=dict)
    fields_from: dict[str, str] = field(default_factory=dict)
    supported: bool = True

    @property
    def key(self):
        return entry_key(self.kind, self.name)


@dataclass(frozen=True)
class Contract:
    entries: dict[str, Entry]
    types: dict[str, Schema]
    upstream_entries: dict[str, Entry] = field(default_factory=dict)
    upstream_raw: dict = field(default_factory=dict)
    deviations: dict[str, list] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def get(self, kind, name):
        return self.entries.get(entry_key(kind, name))


def entry_key(kind, name):
    # Names shared by a command and event (Replay.end) remain distinct.
    return f'{kind}:{name}'


def matches(name, targets):
    return not targets or any(fnmatch.fnmatchcase(name, target if '.' in target else target + '.*')
                              for target in targets)


def field_origins(schema, prefix, origin):
    result = {}
    for name, prop in (schema or {}).get('properties', {}).items():
        path = prefix + '.' + name
        result[path] = origin
        result.update(field_origins(prop['schema'], path, origin))
    if schema and 'items' in schema:
        result.update(field_origins(schema['items'], prefix + '[]', origin))
    if schema and 'values' in schema:
        result.update(field_origins(schema['values'], prefix + '.<key>', origin))
    return result


def read_yaml(path):
    text = path.read_text(encoding='utf-8')
    node = yaml.compose(text)
    lines = {}
    def visit(node, prefix='', ancestors=frozenset()):
        if node is None:
            return
        if id(node) in ancestors or len(ancestors) >= 128:
            raise ValueError('recursive or excessively deep YAML aliases are unsupported')
        ancestors = ancestors | {id(node)}
        lines[prefix] = node.start_mark.line + 1
        if isinstance(node, yaml.MappingNode):
            seen = set()
            for key, value in node.value:
                if not isinstance(key, yaml.ScalarNode) or key.tag != 'tag:yaml.org,2002:str':
                    raise ValueError(f'line {key.start_mark.line + 1}: mapping keys must be strings')
                if key.value in seen:
                    raise ValueError(f'line {key.start_mark.line + 1}: duplicate key {key.value}')
                seen.add(key.value)
                visit(value, f'{prefix}.{key.value}' if prefix else key.value, ancestors)
        elif isinstance(node, yaml.SequenceNode):
            for i, value in enumerate(node.value):
                visit(value, f'{prefix}[{i}]', ancestors)
    visit(node)
    return yaml.safe_load(text), lines


class Loader:
    def __init__(self, repo):
        self.repo = repo
        self.problems = []
        self.source = repo.upstream
        self.lines = {}
        self.entry = ''

    def error(self, code, path, message):
        self.problems.append(Diagnostic(self.repo.relative(self.source),
            self.lines.get(path, self.lines.get('', 1)), code, message, self.entry))

    def schema(self, spec, domain, path):
        if not isinstance(spec, dict):
            self.error('E102', path, 'field schema must be a mapping')
            return {'type': 'any'}
        if '$ref' in spec:
            ref = spec['$ref']
            if not isinstance(ref, str) or not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?', ref):
                self.error('E103', path + '.$ref', 'invalid type reference')
                return {'type': 'any'}
            return {'ref': ref if '.' in ref else domain + '.' + ref}
        kind = spec.get('type')
        accepted = kind.split('|') if isinstance(kind, str) else []
        if not accepted or any(k not in KINDS for k in accepted) or len(set(accepted)) != len(accepted):
            self.error('E102', path + '.type', f'unsupported type {kind!r}; expected one of {", ".join(sorted(KINDS))}')
            # Invalid entries are never exported or validated as a wider type.
            return {'type': 'unsupported'}
        result = {'type': accepted if len(accepted) > 1 else kind}
        if 'enum' in spec:
            values = spec['enum']
            if 'string' not in accepted or not isinstance(values, list) or not values or any(not isinstance(x, str) for x in values) or len(set(values)) != len(values):
                self.error('E102', path + '.enum', 'enum must contain distinct strings on a string field')
            else:
                result['enum'] = values
        if 'array' in accepted:
            # Flat paths may supply the item fields after their array parent.
            if 'items' in spec:
                result['items'] = self.schema(spec['items'], domain, path + '.items')
        if 'object' in accepted and 'properties' in spec:
            result['properties'] = self.fields(spec['properties'], domain, path + '.properties')['properties']
        if 'values' in spec:
            if 'object' not in accepted:
                self.error('E102', path + '.values', 'values requires an object type')
            else:
                result['values'] = self.schema(spec['values'], domain, path + '.values')
        return result

    def fields(self, fields, domain, path, allow_non_object_root=False):
        result = {'type': 'object', 'properties': {}}
        if not isinstance(fields, list):
            self.error('E101', path, f'{path} must be a list')
            return result
        seen = set()
        locations = {}
        for i, spec in enumerate(fields):
            location = f'{path}[{i}]'
            if not isinstance(spec, dict) or not isinstance(spec.get('name'), str):
                self.error('E101', location, 'field needs a string name')
                continue
            name = spec['name']
            if name in seen:
                self.error('E102', location, f'duplicate field {name}')
                continue
            seen.add(name)
            if not isinstance(spec.get('optional', False), bool):
                self.error('E102', location + '.optional', 'optional must be boolean')
            if not isinstance(spec.get('root', False), bool):
                self.error('E102', location + '.root', 'root must be boolean')
            value = self.schema(spec, domain, location)
            locations[id(value)] = location
            if spec.get('root'):
                if len(fields) != 1:
                    self.error('E102', location, 'root must be the only field')
                elif value.get('type') != 'object' and not allow_non_object_root:
                    self.error('E102', location, 'non-object root requires a registered non-object-root violation')
                else:
                    result = value
                    if value.get('type') != 'object':
                        result['violation'] = 'non-object-root'
                continue
            parts = name.split('.')
            cursor = result
            for j, part in enumerate(parts):
                m = re.fullmatch(r'([A-Za-z_$][\w$]*|<key>)(\[\])?', part)
                if not m:
                    self.error('E102', location + '.name', f'invalid field path {name}')
                    break
                key, array = m.groups()
                if key == '<key>':
                    if j == len(parts) - 1:
                        cursor['values'] = {'type': 'array', 'items': value} if array else value
                    else:
                        cursor = cursor.setdefault('values', {'type': 'array'} if array else {'type': 'object', 'properties': {}})
                        if array:
                            if 'array' not in kinds(cursor):
                                self.error('E102', location, '<key> is not an array'); break
                            cursor = cursor.setdefault('items', {'type': 'object', 'properties': {}})
                        if 'object' not in kinds(cursor):
                            self.error('E102', location, '<key> is not an inline object'); break
                    continue
                props = cursor.setdefault('properties', {})
                if j == len(parts) - 1:
                    if array:
                        parent = props.get(key, {}).get('schema', {})
                        if 'array' not in kinds(parent):
                            self.error('E102', location, f'{key} must be declared as an array first')
                        else:
                            parent['items'] = value
                        continue
                    props[key] = {'schema': value}
                    if spec.get('optional') is True:
                        props[key]['optional'] = True
                else:
                    if key not in props:
                        self.error('E102', location + '.name', f'parent field {key} must be declared first')
                        break
                    cursor = props[key]['schema']
                    if array:
                        if 'array' not in kinds(cursor):
                            self.error('E102', location, f'{key} is not an array')
                            break
                        cursor = cursor.setdefault('items', {'type': 'object', 'properties': {}})
                    if 'object' not in kinds(cursor):
                        if any(k in KINDS for k in kinds(cursor)):
                            self.error('E102', location, f'{key} is not an inline object')
                        break
        def arrays(schema, location=path):
            location = locations.get(id(schema), location)
            if 'array' in kinds(schema):
                if 'items' not in schema:
                    self.error('E102', location, 'array requires items or a nested item field')
                    schema['items'] = {'type': 'any'}
                arrays(schema['items'], location + '.items')
            for prop in schema.get('properties', {}).values():
                arrays(prop['schema'], location)
            if 'values' in schema:
                arrays(schema['values'], location)
        arrays(result)
        return result


def load(repo: Repo) -> tuple[Contract, list[Diagnostic]]:
    loader = Loader(repo)
    raw = json.loads(repo.upstream.read_text(encoding='utf-8'))
    types, upstream, entries = {}, {}, {}
    invalid_types = set()
    for domain in raw.get('domains', []):
        name = domain['domain']
        for spec in domain.get('types', []):
            loader.entry = name + '.' + spec['id']
            start = len(loader.problems)
            types[name + '.' + spec['id']] = loader.schema(spec, name, '')
            if len(loader.problems) != start:
                invalid_types.add(loader.entry)
        for section, kind in [('commands', 'method'), ('events', 'event')]:
            for item in domain.get(section, []):
                full = name + '.' + item['name']
                loader.entry = full
                start = len(loader.problems)
                entry = Entry(full, kind, loader.fields(item.get('parameters', []), name, 'parameters'),
                              loader.fields(item.get('returns', []), name, 'returns') if kind == 'method' else None,
                              [], 'upstream', document=item)
                provenance = {**field_origins(entry.params, 'params', 'upstream'),
                              **field_origins(entry.returns, 'returns', 'upstream')}
                upstream[entry.key] = replace(entry, supported=len(loader.problems) == start,
                                              fields_from=provenance)
    pristine = deepcopy(upstream)
    deviations, violations = {}, {}
    if repo.support.exists():
        loader.source, loader.entry = repo.support, ''
        try:
            support, loader.lines = read_yaml(repo.support)
            if not isinstance(support, dict):
                raise ValueError('support must be a mapping')
        except (yaml.YAMLError, ValueError) as error:
            loader.error('E501', '', str(error)); support = {}
        for domain, config in support.items():
            if not isinstance(config, dict) or set(config) - {'deviations', 'violations'} or any(not isinstance(value, list) for value in config.values()):
                loader.error('E501', str(domain), 'support domains may contain deviations and violations lists')
                continue
            deviations[domain] = config.get('deviations', [])
            for i, change in enumerate(config.get('deviations', [])):
                path = f'{domain}.deviations[{i}]'
                apply_deviation(loader, upstream, domain, change, path, types)
            for i, violation in enumerate(config.get('violations', [])):
                path = f'{domain}.violations[{i}]'
                loader.entry = ''
                if (not isinstance(violation, dict) or set(violation) != {'target', 'kind', 'reason'}
                        or violation.get('kind') != 'non-object-root'
                        or not isinstance(violation.get('target'), str)
                        or not re.fullmatch(r'\w+\.(parameters|returns)', violation['target'])
                        or not isinstance(violation.get('reason'), str) or not violation['reason'].strip()):
                    loader.error('E501', path, 'invalid non-object-root violation'); continue
                key = domain + '.' + violation['target']
                if key in violations:
                    loader.error('E501', path, f'duplicate violation {key}'); continue
                violations[key] = (violation, loader.lines.get(path, 1))
    for path in sorted(repo.docs.glob('*/*/*.yaml')):
        section = path.parent.name
        if section not in ('methods', 'events'):
            continue
        domain, name = path.parents[1].name, path.stem
        full, kind = domain + '.' + name, 'method' if section == 'methods' else 'event'
        loader.source, loader.entry, loader.lines = path, full, {}
        start = len(loader.problems)
        try:
            doc, loader.lines = read_yaml(path)
            if not isinstance(doc, dict):
                raise ValueError('document must be a mapping')
        except (yaml.YAMLError, ValueError) as error:
            loader.error('E101', '', str(error))
            empty = {'type': 'object', 'properties': {}}
            entries[entry_key(kind, full)] = Entry(
                full, kind, empty, empty if kind == 'method' else None, [], 'local',
                source=path, supported=False)
            continue
        allowed = {'summary','parameters','returns','events','examples','notes','since','experimental','deprecated','capture'}
        for key in set(doc) - allowed:
            loader.error('E101', key, f'unknown document key {key}')
        if doc.get('capture', 'unsafe') not in ('safe', 'unsafe'):
            loader.error('E101', 'capture', 'capture must be safe or unsafe')
        if not isinstance(doc.get('summary'), str) or not doc['summary'].strip() or 'TODO' in doc['summary']:
            loader.error('E101', 'summary', 'summary must describe the entry; replace placeholder text')
        for key in ('notes','examples','events'):
            if key in doc and not isinstance(doc[key], list):
                loader.error('E101', key, f'{key} must be a list')
        original = upstream.get(entry_key(kind, full))
        params = loader.fields(doc.get('parameters', []), domain, 'parameters', full + '.parameters' in violations)
        returns = loader.fields(doc.get('returns', []), domain, 'returns', full + '.returns' in violations) if kind == 'method' else None
        provenance = dict(original.fields_from) if original else {}
        for section, schema in [('params', params), ('returns', returns)]:
            if schema is None:
                continue
            base = getattr(original, section) if original else None
            local = deepcopy(schema)
            local_section = 'parameters' if section == 'params' else 'returns'
            fields = doc.get(local_section, [])
            has_root = isinstance(fields, list) and any(isinstance(f, dict) and f.get('root') for f in fields)
            if base and not has_root:
                schema.clear(); schema.update(deepcopy(base))
                schema.setdefault('properties', {}).update(local.get('properties', {}))
                for field_name in base.get('properties', {}):
                    provenance.setdefault(section + '.' + field_name, 'upstream')
            for field_name in local.get('properties', {}):
                prefix = section + '.' + field_name
                provenance = {key: origin for key, origin in provenance.items()
                              if not (key == prefix or key.startswith(prefix + '.') or key.startswith(prefix + '[]'))}
            provenance.update(field_origins(local, section, 'local'))
        examples = doc.get('examples') if isinstance(doc.get('examples'), list) else []
        entry = Entry(full, kind, params, returns, examples or [], 'both' if original else 'local',
                      path, loader.lines.copy(), doc, provenance, len(loader.problems) == start)
        entries[entry.key] = entry
    for key, entry in upstream.items():
        if entry != pristine[key] and key not in entries:
            entries[key] = entry
    # Validate upstream entries too: registration-only entries are added by
    # inventory.complete later and must never bypass reference checks.
    for table, key, entry in [(table, key, entry) for table in (upstream, entries)
                              for key, entry in list(table.items())]:
        if not entry.supported:
            continue
        loader.source = entry.source or repo.upstream
        loader.lines, loader.entry = entry.line_of, entry.name
        start = len(loader.problems)
        visited = set()
        def resolve(schema, aliases=frozenset()):
            if 'ref' in schema:
                ref = schema['ref']
                if ref in aliases:
                    loader.error('E103', '', f'reference-only cycle at {ref}')
                    return
                if ref not in types:
                    loader.error('E103', '', f'unresolved type reference {ref}')
                elif ref in invalid_types:
                    loader.error('E103', '', f'referenced type has an invalid schema: {ref}')
                elif ref not in visited or 'ref' in types[ref]:
                    visited.add(ref)
                    resolve(types[ref], aliases | {ref})
                return
            if any(k not in KINDS for k in kinds(schema)):
                loader.error('E103', '', 'referenced schema uses an unsupported type')
            if 'array' in kinds(schema):
                if 'items' not in schema:
                    loader.error('E103', '', 'referenced array schema requires items')
                else:
                    resolve(schema['items'])
            for prop in schema.get('properties', {}).values():
                resolve(prop['schema'])
            if 'values' in schema:
                resolve(schema['values'])
        resolve(entry.params)
        if entry.returns is not None:
            resolve(entry.returns)
        if len(loader.problems) != start:
            table[key] = replace(entry, supported=False)
    for target, (violation, line) in violations.items():
        name, section = target.rsplit('.', 1)
        schemas = [entry.params if section == 'parameters' else entry.returns
                   for entry in entries.values() if entry.name == name]
        roots = [schema for schema in schemas if schema and schema.get('violation') == 'non-object-root']
        code, severity, message = ('W501', 'warning', f'{target}: non-object-root: {violation["reason"]}')
        if not schemas:
            code, severity, message = 'E501', 'error', f'violation target does not exist: {target}'
        elif not roots:
            code, severity, message = 'E502', 'error', f'non-object-root violation no longer applies: {target}'
        elif len(roots) > 1:
            code, severity, message = 'E501', 'error', f'ambiguous violation target: {target}'
        loader.problems.append(Diagnostic(repo.relative(repo.support), line, code, message, name, severity))
    result = Contract(entries, types, upstream, raw, deviations, loader.problems)
    return result, loader.problems


def apply_deviation(loader, entries, domain, change, path, types):
    if not isinstance(change, dict) or set(change) - {'target','kind','as','reason'}:
        loader.error('E501', path, 'invalid deviation mapping'); return
    target, kind = change.get('target', ''), change.get('kind')
    if not isinstance(target, str) or not isinstance(change.get('reason'), str) or not change['reason'].strip():
        loader.error('E501', path, 'deviation requires a target and reason'); return
    parts = target.split('.')
    if len(parts) < 3 or parts[1] not in ('parameters','returns') or kind not in ('added','omitted','retyped'):
        loader.error('E501', path, f'invalid deviation target or kind: {target}'); return
    loader.entry = domain + '.' + parts[0]
    candidates = [entry for entry in entries.values() if entry.name == loader.entry]
    if len(candidates) != 1:
        loader.error('E501', path, f'upstream entry is missing or ambiguous: {target}'); return
    entry = candidates[0]
    schema = entry.params if parts[1] == 'parameters' else entry.returns
    # Materialize only this entry's path. Mutating a shared upstream type would
    # silently change contracts for unrelated methods and events.
    for part in [*parts[2:-1], None]:
        visited = set()
        while schema and 'ref' in schema:
            ref = schema['ref']
            if ref in visited or ref not in types:
                loader.error('E501', path, f'unresolvable deviation parent: {target}'); return
            visited.add(ref)
            schema.clear(); schema.update(deepcopy(types[ref]))
        if part is not None:
            schema = (schema or {}).get('properties', {}).get(part, {}).get('schema')
    if schema is None or 'properties' not in schema:
        loader.error('E501', path, f'upstream parent does not exist: {target}'); return
    props, field_name = schema['properties'], parts[-1]
    if (kind == 'added') == (field_name in props):
        loader.error('E501', path, f'{kind} requires field to {"not " if kind == "added" else ""}exist upstream: {target}'); return
    if kind == 'omitted':
        del props[field_name]
    else:
        spec = change.get('as')
        start = len(loader.problems)
        value = loader.schema(spec, domain, path + '.as')
        if len(loader.problems) != start:
            return
        prop = {'schema': value}
        optional = spec.get('optional', props.get(field_name, {}).get('optional', False))
        if not isinstance(optional, bool):
            loader.error('E501', path, 'deviation optional must be boolean'); return
        if optional:
            prop['optional'] = True
        if props.get(field_name) == prop:
            loader.error('E502', path, f'deviation no longer differs from upstream: {target}'); return
        props[field_name] = prop
    provenance = dict(entry.fields_from)
    provenance[('params' if parts[1] == 'parameters' else 'returns') + '.' + '.'.join(parts[2:])] = 'deviation'
    entries[entry.key] = replace(entry, fields_from=provenance)

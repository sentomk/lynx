# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Source registration inventory; scans are evidence of registration, not execution."""
from dataclasses import dataclass, field, replace
from pathlib import Path
import re
import yaml

from cdpc.model import Contract, Diagnostic, Repo, entry_key

ENGINE_DOMAINS = {'Debugger', 'Runtime', 'Profiler', 'HeapProfiler'}
METHOD = r'[A-Za-z][A-Za-z0-9]*\.[A-Za-z][A-Za-z0-9_]*'


@dataclass(frozen=True)
class Registration:
    name: str
    file: str
    line: int


@dataclass
class Inventory:
    methods: dict[str, Registration] = field(default_factory=dict)
    events: dict[str, Registration] = field(default_factory=dict)
    scopes: dict[str, str] = field(default_factory=dict)
    problems: list[Diagnostic] = field(default_factory=list)
    version: str = '0.0'
    primjs_missing: bool = False


def body(text, start):
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if not depth:
                return text[start + 1:i]
    raise ValueError('unterminated registration function')


def scan(repo: Repo, contract: Contract, strict=False) -> Inventory:
    result = Inventory()
    def add(path, name, position, text, table):
        location = Registration(name, repo.relative(path), text[:position].count('\n') + 1)
        if table is result.methods and name in table and table[name].file != location.file:
            result.problems.append(Diagnostic(location.file, location.line, 'E401',
                f'{name} is registered in multiple files', name))
        table.setdefault(name, location)
    agent_dir = repo.root / 'devtool/lynx_devtool/agent/domain_agent'
    if not agent_dir.exists():
        raise OSError(f'missing domain agent directory: {agent_dir}')
    for path in sorted(agent_dir.glob('*.cc')):
        if '_unittest' in path.name:
            continue
        text = path.read_text()
        found = list(re.finditer(rf'functions_map_\["({METHOD})"\]', text))
        for func in re.finditer(r'\bvoid\s+\w+::CallMethod\s*\([^)]*\)\s*\{', text):
            block = body(text, func.end() - 1)
            for match in re.finditer(rf'\bif\s*\(\s*method\s*==\s*"({METHOD})"', block):
                name = match.group(1)
                if name.split('.')[0] not in ENGINE_DOMAINS:
                    add(path, name, func.end() + match.start(), text, result.methods)
        for match in found:
            name = match.group(1)
            if name.split('.')[0] not in ENGINE_DOMAINS:
                add(path, name, match.start(), text, result.methods)
    primjs = repo.root / 'third_party/quickjs/src/src/inspector/protocols.cc'
    if primjs.exists():
        text = primjs.read_text()
        func = re.search(r'const\s+debug_function_type\s*&\s*GetDebugFunctionMap\s*\(\)\s*\{', text)
        if func is None:
            raise ValueError(f'cannot find GetDebugFunctionMap in {primjs}')
        block = body(text, func.end() - 1)
        for match in re.finditer(rf'\{{\s*"({METHOD})"\s*,', block):
            add(primjs, match.group(1), func.end() + match.start(), text, result.methods)
    else:
        result.primjs_missing = True
        result.problems.append(Diagnostic(repo.relative(primjs), 1, 'E403',
            'PrimJS source missing; its registration inventory is unverified; run tools/hab sync .',
            severity='error' if strict else 'warning'))
        if repo.manifest.exists():
            prior = yaml.safe_load(repo.manifest.read_text()) or {}
            for domain in prior.get('domains', []):
                for method in domain.get('methods', []):
                    if method.get('source') == repo.relative(primjs):
                        name = domain['name'] + '.' + method['name']
                        result.methods[name] = Registration(name, method['source'], 1)
    source = repo.root / 'devtool/lynx_devtool/lynx_devtool_ng.cc'
    text = source.read_text()
    scopes = {}
    for func in re.finditer(r'void\s+LynxDevToolNG::Register(Global|Instance)DomainAgents\s*\([^)]*\)\s*\{', text):
        for domain in re.findall(r'RegisterAgent\s*\(\s*"(\w+)"', body(text, func.end()-1)):
            scopes.setdefault(domain, set()).add(func.group(1).lower())
    result.scopes = {name: '-and-'.join(sorted(values)) for name, values in scopes.items()}
    version = repo.root / 'core/renderer/tasm/config.h'
    if version.exists():
        match = re.search(r'#define\s+LYNX_VERSION\s+tasm::V_(\d+)_(\d+)', version.read_text())
        if match:
            result.version = '.'.join(match.groups())
    known_methods = set(result.methods) | {
        e.name for e in [*contract.entries.values(), *contract.upstream_entries.values()]
        if e.kind == 'method'}
    known_events = {e.name for e in [*contract.entries.values(), *contract.upstream_entries.values()] if e.kind == 'event'}
    for root in (repo.root / 'devtool', repo.root / 'platform'):
        for path in sorted(root.rglob('*')):
            if path.suffix not in {'.cc','.h','.mm','.m','.java','.kt','.ets'}:
                continue
            if '.generated.' in path.name or 'unittest' in path.name or '_test.' in path.name or set(path.parts) & {'tests','testing','testdata','test','androidTest'}:
                continue
            text = path.read_text(errors='replace')
            event_positions = set()
            # Unknown names need an event-shaped use. Treating every dotted
            # string as an event also picks up includes, log tags and filenames.
            for pattern in (
                rf'\[\s*"method"\s*\]\s*=\s*"({METHOD})"',
                rf'\bput\s*\(\s*"method"\s*,\s*"({METHOD})"',
                rf'\bmethod\s*:\s*"({METHOD})"',
                rf'\b(?:SendCDPEvent|SendEvent|SendNotification)\s*\(\s*"({METHOD})"',
            ):
                for match in re.finditer(pattern, text):
                    event_positions.add(match.start(1))
            for match in re.finditer(rf'"({METHOD})"', text):
                name = match.group(1)
                if name in known_events or (match.start(1) in event_positions and name not in known_methods):
                    add(path, name, match.start(), text, result.events)
    return result


def complete(contract: Contract, inventory: Inventory) -> Contract:
    entries = dict(contract.entries)
    selected = [('method', name) for name in inventory.methods] + [('event', name) for name in inventory.events]
    for entry in contract.entries.values():
        for ref in entry.document.get('events', []):
            selected.append(('event', ref.get('name') if isinstance(ref, dict) else ref))
        for example in entry.examples:
            if not isinstance(example, dict):
                continue
            for key in ('event', 'response'):
                value = example.get(key)
                if isinstance(value, dict) and 'method' in value and 'id' not in value:
                    selected.append(('event', value['method']))
    for kind, name in selected:
        key = entry_key(kind, name)
        if key not in entries and key in contract.upstream_entries:
            entries[key] = contract.upstream_entries[key]
    return replace(contract, entries=entries)


def check(repo, contract, inventory):
    problems = list(inventory.problems)
    for name, registration in inventory.methods.items():
        if contract.get('method', name) is None:
            problems.append(Diagnostic(registration.file, registration.line, 'E401',
                f'{name} is registered but has no contract', name))
    for entry in contract.entries.values():
        if entry.kind == 'method' and entry.source and entry.name not in inventory.methods:
            if inventory.primjs_missing and entry.name.split('.')[0] in ENGINE_DOMAINS:
                continue
            problems.append(Diagnostic(repo.relative(entry.source), 1, 'E402',
                f'{entry.name} has a local contract but is not registered', entry.name))
    return problems

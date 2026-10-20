# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Stream recorded traffic against YAML contracts, without generated data."""
from collections import OrderedDict
from dataclasses import dataclass, field
import json
from pathlib import Path
import sys

from cdpc.lint import ERROR_SCHEMA
from cdpc.model import matches
from cdpc.schema import check


@dataclass(frozen=True)
class Violation:
    file: str
    line: int
    code: str
    message: str
    entry: str = ''
    severity: str = 'error'
    id: int = 0
    problems: list[str] = field(default_factory=list)


def signed_id(value):
    return type(value) is int and -(2**63) <= value < 2**63


class Checker:
    def __init__(self, contract, file, targets=()):
        self.contract, self.file, self.targets = contract, file, targets
        self.pending, self.completed = OrderedDict(), OrderedDict()
        self.violations = []
        self.overflow_reported = False
        self.line = 0
        self.messages = 0

    def report(self, kind, method, id, problems):
        if matches(method, self.targets):
            self.violations.append(Violation(
                self.file, self.line, kind, f'{method} (id {id}): ' + '; '.join(problems),
                method, 'error', id, problems))

    def fields(self, kind, entry, id, schema, value, strict, path):
        problems = check(schema, value, strict=strict, types=self.contract.types, path=path)
        if problems:
            self.report(kind, entry, id, [str(p) for p in problems])

    def entry(self, kind, method, id):
        entry = self.contract.get(kind, method)
        if entry is None:
            self.report('kUnknownMethod' if kind == 'method' else 'kUnknownEvent',
                        method, id, [f'{kind} has no contract'])
        elif not entry.supported:
            self.report('E102', method, id, ['contract uses an unsupported schema; run cdpc check ' + method])
            return None
        return entry

    def accept(self, row, line):
        self.line = line
        if (not isinstance(row, dict) or type(row.get('t')) not in (int, float)
                or not isinstance(row.get('channel'), str)
                or row.get('dir') not in ('in', 'out', 'dropped')
                or not isinstance(row.get('truncated', False), bool)):
            raise ValueError(f'{self.file}:{line}: invalid trace envelope')
        self.messages += 1
        channel, direction = row['channel'], row['dir']
        if direction == 'dropped':
            if not signed_id(row.get('id')):
                raise ValueError(f'{self.file}:{line}: dropped record needs a signed integer id')
            key = channel, row['id']
            pending = self.pending.get(key)
            if pending and not pending['dropped']:
                pending['dropped'] = True
                self.report('kDropped', pending['method'], key[1],
                            ['responder destroyed without an explicit reply'])
            return
        message = row.get('message')
        if not isinstance(message, dict):
            raise ValueError(f'{self.file}:{line}: message must be an object')
        truncated = row.get('truncated', False)
        params = message.get('params')
        params = {} if params is None else params
        if direction == 'in':
            if not signed_id(message.get('id')) or not isinstance(message.get('method'), str):
                return  # Like the C++ observer, ignore malformed request envelopes.
            id, method = message['id'], message['method']
            key = channel, id
            previous = self.pending.pop(key, None)
            if previous:
                self.report('kUnanswered', previous['method'], id,
                            ['request id reused before a response'])
            if len(self.pending) >= 4096:
                oldest, request = self.pending.popitem(last=False)
                if not self.overflow_reported:
                    self.report('kUnanswered', request['method'], oldest[1],
                                ['pending request limit exceeded; oldest request evicted'])
                    self.overflow_reported = True
            self.completed.pop(key, None)
            entry = self.entry('method', method, id)
            self.pending[key] = {'method': method, 'entry': entry, 'dropped': False}
            if entry and not truncated:
                self.fields('kInvalidParams', method, id, entry.params, params, False, 'params')
        elif 'id' not in message:
            method = message.get('method')
            if not isinstance(method, str):
                return
            entry = self.entry('event', method, 0)
            if entry and not truncated:
                self.fields('kInvalidEvent', method, 0, entry.params, params, True, 'params')
        elif signed_id(message['id']):
            id = message['id']
            key = channel, id
            request = self.pending.pop(key, None)
            if request is None:
                self.report('kDuplicateResponse', self.completed.get(key, ''), id,
                            ['response has no outstanding request on this channel'])
                return
            method, entry = request['method'], request['entry']
            self.completed[key] = method
            if len(self.completed) > 4096:
                self.completed.popitem(last=False)
            if truncated or request['dropped']:
                return
            if ('result' in message) == ('error' in message):
                self.report('kInvalidResult', method, id,
                            ['response must contain exactly one of result or error'])
            elif 'error' in message:
                self.fields('kInvalidError', method, id, ERROR_SCHEMA, message['error'], True, 'error')
            elif entry:
                self.fields('kInvalidResult', method, id, entry.returns, message['result'], True, 'result')

    def flush(self):
        for (_, id), request in self.pending.items():
            if not request['dropped']:
                self.report('kUnanswered', request['method'], id, ['request has no response'])
        self.pending.clear()
        self.completed.clear()


def reject_constant(value):
    raise ValueError(f'non-JSON numeric constant {value}')


def verify(contract, paths, targets=(), stdin=None):
    problems, count = [], 0
    for path in paths:
        checker = Checker(contract, str(path), targets)
        stream = stdin or sys.stdin if str(path) == '-' else Path(path).open(encoding='utf-8')
        try:
            for line, text in enumerate(stream, 1):
                if not text.strip():
                    continue
                try:
                    row = json.loads(text, parse_constant=reject_constant)
                except ValueError as error:
                    raise ValueError(f'{path}:{line}: invalid JSON: {error}') from error
                checker.accept(row, line)
        finally:
            if str(path) != '-':
                stream.close()
        checker.flush()
        count += checker.messages
        problems += checker.violations
    return problems, count


def split_inputs(inputs):
    """Existing files and '-' are traces; trailing domains/globs are filters."""
    paths, targets = [], []
    for value in inputs:
        if value == '-' or Path(value).is_file():
            if targets:
                raise ValueError('trace files must precede target filters')
            paths.append(value)
        elif not paths or '/' in value or value.endswith(('.jsonl', '.json')):
            raise OSError(f'trace file not found: {value}')
        else:
            targets.append(value)
    return paths, targets

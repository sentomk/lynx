# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""The contract JSON type system. No repository or CLI dependencies."""
from dataclasses import dataclass
import math
import sys
from typing import Any

Schema = dict[str, Any]
KINDS = frozenset({'string', 'integer', 'number', 'boolean', 'object', 'array', 'any'})


@dataclass(frozen=True)
class Problem:
    path: str
    message: str

    def __str__(self):
        return f'{self.path}: {self.message}'


def check(schema: Schema, value: Any, *, strict: bool, types: dict[str, Schema],
          path: str = '', _depth: int = 0) -> list[Problem]:
    """Check in sorted field order, matching ContractChecker's diagnostic text."""
    def error(message):
        return [Problem(path, message)]
    if _depth >= 128:
        return error('maximum schema depth exceeded')
    if 'ref' in schema:
        ref = schema['ref']
        if ref not in types:
            return error(f'unresolved type reference {ref}')
        return check(types[ref], value, strict=strict, types=types,
                     path=path, _depth=_depth + 1)
    kind = schema.get('type')
    integral = (type(value) is int and -(2**63) <= value <= 2**64 - 1) or (
        type(value) is float and math.isfinite(value) and value.is_integer()
        and -(2**63) <= value < 2**64)
    matches = {
        'any': True, 'string': isinstance(value, str), 'integer': integral,
        'number': (type(value) is int and abs(value) <= sys.float_info.max) or
                  (type(value) is float and math.isfinite(value)),
        'boolean': type(value) is bool, 'object': isinstance(value, dict),
        'array': isinstance(value, list),
    }
    if not matches.get(kind, False):
        return error(f'expected {kind}')
    if kind == 'string' and 'enum' in schema and value not in schema['enum']:
        return error(f"'{value}' is not an allowed value")
    problems = []
    if kind == 'array':
        for i, item in enumerate(value):
            problems += check(schema['items'], item, strict=strict, types=types,
                              path=f'{path}[{i}]', _depth=_depth + 1)
    if kind == 'object' and 'properties' in schema:
        for name, prop in sorted(schema['properties'].items()):
            field_path = f'{path}.{name}' if path else name
            if name not in value:
                if not prop.get('optional', False):
                    problems.append(Problem(field_path, 'missing required field'))
            else:
                problems += check(prop['schema'], value[name], strict=strict, types=types,
                                  path=field_path, _depth=_depth + 1)
        if strict:
            for name in sorted(value.keys() - schema['properties'].keys()):
                problems.append(Problem(f'{path}.{name}' if path else name, 'unexpected field'))
    return problems


def references(schema: Schema):
    if 'ref' in schema:
        yield schema['ref']
    for prop in schema.get('properties', {}).values():
        yield from references(prop['schema'])
    if 'items' in schema:
        yield from references(schema['items'])

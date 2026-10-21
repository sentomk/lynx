# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Contract declarations and examples. Diagnostics retain YAML locations."""
from cdpc.model import Diagnostic
from cdpc.schema import check as check_value, params_value, example_request

ERROR_SCHEMA = {'type': 'object', 'properties': {
    'code': {'schema': {'type': 'integer'}},
    'message': {'schema': {'type': 'string'}},
    'data': {'schema': {'type': 'any'}, 'optional': True},
}}


def check(repo, contract):
    problems = list(contract.diagnostics)
    for entry in contract.entries.values():
        if not entry.source or not entry.supported:
            continue
        for i, example in enumerate(entry.examples):
            path = f'examples[{i}]'
            def report(code, message, suffix=''):
                problems.append(Diagnostic(repo.relative(entry.source), entry.line_of.get(path + suffix, entry.line_of.get(path, 1)), code,
                    f'example {example.get("title", i) if isinstance(example, dict) else i!r}: {message}', entry.name))
            if not isinstance(example, dict) or not isinstance(example.get('title'), str) or not any(k in example for k in ('request','response','event')):
                report('E201', 'example needs a title and a request, response or event'); continue
            request = example.get('request')
            if entry.document.get('capture') == 'safe':
                request = example_request(request)
            if request is not None:
                if not isinstance(request, dict) or request.get('method') != entry.name or type(request.get('id')) is not int:
                    report('E201', 'request needs the entry method and an integer id', '.request')
                else:
                    params = params_value(entry.params, request)
                    response = example.get('response')
                    expected_error = response.get('error', {}) if isinstance(response, dict) else {}
                    invalid_params_example = isinstance(expected_error, dict) and expected_error.get('code') in (-32602, -32600)
                    for problem in ([] if invalid_params_example else check_value(entry.params, params, strict=False, types=contract.types, path='params')):
                        report('E202', str(problem), '.request.params')
            for direction in ('response','event'):
                if direction not in example:
                    continue
                value = example[direction]
                if not isinstance(value, dict):
                    report('E201', f'{direction} must be an object', '.' + direction); continue
                if 'method' in value and 'id' not in value:
                    event = contract.get('event', value['method'])
                    if event is None or not event.supported:
                        report('E201', f'event has no supported contract: {value["method"]}', '.' + direction); continue
                    schema, data, field = event.params, params_value(event.params, value), 'params'
                else:
                    if direction == 'event' or type(value.get('id')) is not int or ('result' in value) == ('error' in value):
                        report('E201', 'response needs id and exactly one of result or error', '.' + direction); continue
                    if isinstance(request, dict) and value['id'] != request.get('id'):
                        report('E201', 'response id differs from request id', '.response.id')
                    field = 'error' if 'error' in value else 'result'
                    schema, data = ERROR_SCHEMA if field == 'error' else entry.returns, value[field]
                if schema is not None:
                    for problem in check_value(schema, data, strict=True, types=contract.types, path=field):
                        report('E202', str(problem), '.' + direction + '.' + field)
    return problems

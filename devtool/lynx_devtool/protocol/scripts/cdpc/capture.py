# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Opt-in example replay using the optional agent-lynx CLI."""
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from cdpc.model import matches
from cdpc.schema import example_request


def send(executable, client, session, request):
    command = [executable, '--json', 'cdp', '--client', client, '--session', session,
               '--method', request['method'], json.dumps(request.get('params', {}), allow_nan=False)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30,
            env={**os.environ, 'AGENT_LYNX_DISABLE_UPDATE_NOTICE': '1'})
    except subprocess.TimeoutExpired as error:
        raise OSError(f'agent-lynx timed out for {request["method"]}; partial trace retained') from error
    if result.returncode == 0:
        try:
            payload = json.loads(result.stdout)
        except ValueError as error:
            raise OSError('agent-lynx returned invalid JSON; partial trace retained') from error
        # agent-lynx cdp prints the result value (including scalar roots).
        return {'id': request['id'], 'result': payload}
    for text in (result.stdout, result.stderr):
        try:
            payload = json.loads(text)
        except ValueError:
            continue
        error = payload.get('error', payload) if isinstance(payload, dict) else None
        if isinstance(error, dict) and type(error.get('code')) is int and isinstance(error.get('message'), str):
            return {'id': request['id'], 'error': {key: error[key] for key in ('code', 'message', 'data') if key in error}}
    # Older agent-lynx versions print thrown errors as human-readable stacks.
    # Do not invent a CDP code by parsing those strings.
    raise OSError(f'agent-lynx failed for {request["method"]} without a structured JSON error; '
                  'use a version with JSON error output; partial trace retained')


def capture(contract, targets, client, session, output=None):
    executable = shutil.which('agent-lynx')
    if not executable:
        raise OSError('agent-lynx is required for capture; install with npm install -g @byted/agent-lynx')
    selected = [entry for entry in sorted(contract.entries.values(), key=lambda e: e.name)
                if entry.kind == 'method' and entry.supported and matches(entry.name, targets)
                and entry.document.get('capture', 'unsafe') == 'safe']
    requests = [example['request'] for entry in selected for example in entry.examples
                if isinstance(example, dict) and isinstance(example.get('request'), dict)]
    if not requests:
        raise ValueError('no safe method examples match; mark reviewed read-only methods capture: safe')
    path = Path(output) if output else Path(datetime.now().strftime('cdpc-%Y%m%d-%H%M%S-%f.trace.jsonl'))
    channel, next_id, bindings = f'agent-lynx:{client}:{session}', 1, {}
    with path.open('x', encoding='utf-8') as stream:
        def exchange(request):
            nonlocal next_id
            request = {**request, 'id': next_id}
            next_id += 1
            def record(direction, message):
                stream.write(json.dumps({'t': time.time_ns() // 1_000_000, 'dir': direction,
                                        'channel': channel, 'message': message}, ensure_ascii=False, allow_nan=False) + '\n')
                stream.flush()
            record('in', request)
            response = send(executable, client, session, request)
            record('out', response)
            return response
        for request in requests:
            if '$rootNodeId' in json.dumps(request) and '$rootNodeId' not in bindings:
                entry = contract.get('method', 'DOM.getDocument')
                if not entry or not entry.supported or entry.document.get('capture') != 'safe':
                    raise ValueError('$rootNodeId requires a supported, capture: safe DOM.getDocument contract')
                response = exchange({'method': 'DOM.getDocument', 'params': {'depth': 0}})
                result = response.get('result')
                root = result.get('root') if isinstance(result, dict) else None
                if not isinstance(root, dict) or type(root.get('nodeId')) is not int:
                    raise ValueError('DOM.getDocument did not return a root node id; partial trace retained')
                bindings['$rootNodeId'] = root['nodeId']
            exchange(example_request(request, bindings))
    return path, len(requests)

# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Load exactly the consumer-pinned CDPC checkout. Never clone during a build."""
from functools import lru_cache
import importlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys

PIN = Path(__file__).resolve().parents[1] / 'cdpc_toolchain.json'


def git(source, *args):
    result = subprocess.run(['git', '-C', str(source), *args], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise OSError('cannot inspect CDPC checkout: ' + result.stderr.strip())
    return result.stdout.strip()


@lru_cache(maxsize=1)
def activate():
    pin = json.loads(PIN.read_text(encoding='utf-8'))
    if (not isinstance(pin, dict) or pin.get('version') != 1
            or not isinstance(pin.get('revision'), str)
            or not re.fullmatch(r'[0-9a-f]{40}', pin['revision'])
            or not isinstance(pin.get('repository'), str)
            or not isinstance(pin.get('tool_version'), str)
            or pin.get('contract_format') != 2 or pin.get('descriptor_format') != 1):
        raise ValueError('invalid or unsupported CDPC toolchain pin format')
    lynx_root = Path(__file__).resolve().parents[4]
    source = Path(os.environ.get('CDPC_SOURCE_DIR', str(lynx_root.parent/'cdpc'))).expanduser().resolve()
    hint = f'Use a clean checkout of {pin["repository"]} at {pin["revision"]} and set CDPC_SOURCE_DIR.'
    if not (source/'python/cdpc_core/__init__.py').is_file():
        raise OSError('CDPC source is not available. ' + hint)
    if git(source, 'rev-parse', 'HEAD') != pin['revision']:
        raise OSError('CDPC revision differs from the consumer pin. ' + hint)
    dirty = git(source, 'status', '--porcelain', '--untracked-files=all', '--',
                'python/cdpc_core', 'pyproject.toml', 'include', 'src')
    if dirty:
        raise OSError('CDPC tooling/runtime source has uncommitted changes. ' + hint)
    # CLI execution should not mutate the dependency checkout with bytecode.
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source/'python'))
    package = importlib.import_module('cdpc_core')
    if (not Path(package.__file__).resolve().is_relative_to(source/'python')
            or package.__version__ != pin['tool_version']):
        raise OSError('loaded CDPC package does not match the consumer pin')
    return source


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        activate()
        if args[:1] == ['runtime']:
            from cdpc_runtime import main as run
            return run(args[1:])
        from cdpc.__main__ import main as run
        return run(args)
    except (OSError, ValueError) as error:
        problem = {'file': str(PIN), 'line': 1, 'code': 'ENV', 'message': str(error),
                   'entry': '', 'severity': 'error'}
        if any(args[i:i+2] == ['--format','json'] for i in range(len(args))):
            print(json.dumps({'problems':[problem], 'summary':'cdpc: toolchain unavailable'},indent=2))
        else:
            print(f'cdpc: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

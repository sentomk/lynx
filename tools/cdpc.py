#!/usr/bin/env python3
"""Load Lynx's pinned dependency without user environment configuration."""
import importlib
import json
from pathlib import Path
import re
import subprocess
import sys


def git(source, *args):
    result = subprocess.run(['git', '-C', str(source), *args], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise OSError('cannot inspect CDPC dependency: ' + result.stderr.strip())
    return result.stdout.strip()


def activate(root):
    pin_path = root / 'devtool/lynx_devtool/protocol/cdpc_toolchain.json'
    pin = json.loads(pin_path.read_text(encoding='utf-8'))
    if (not isinstance(pin, dict) or pin.get('version') != 1
            or not isinstance(pin.get('revision'), str)
            or not re.fullmatch(r'[0-9a-f]{40}', pin['revision'])
            or not isinstance(pin.get('repository'), str)
            or not isinstance(pin.get('tool_version'), str)
            or pin.get('contract_format') != 2 or pin.get('descriptor_format') != 1):
        raise ValueError(f'invalid or unsupported CDPC pin: {pin_path}')
    source = root / 'third_party/cdpc'
    hint = f'Run tools/hab sync . in {root}; DEPS must fetch CDPC at {pin["revision"]}.'
    if not (source / 'python/cdpc_lynx/__main__.py').is_file():
        raise OSError(f'CDPC Lynx tooling is unavailable at {source}. ' + hint)
    if Path(git(source, 'rev-parse', '--show-toplevel')).resolve() != source.resolve():
        raise OSError('third_party/cdpc must be its own Git checkout. ' + hint)
    if git(source, 'rev-parse', 'HEAD') != pin['revision']:
        raise OSError('CDPC revision differs from the consumer pin. ' + hint)
    if git(source, 'status', '--porcelain', '--untracked-files=all', '--',
           'python', 'include', 'src', 'tools', 'pyproject.toml'):
        raise OSError('CDPC tooling/runtime dependency has uncommitted changes; commit or restore them before use.')
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source / 'python'))
    package = importlib.import_module('cdpc_core')
    if (not Path(package.__file__).resolve().is_relative_to(source.resolve() / 'python')
            or package.__version__ != pin['tool_version']):
        raise OSError('loaded CDPC package does not match the consumer pin')


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(__file__).resolve().parents[1]
    try:
        activate(root)
        from cdpc_lynx.__main__ import main as run
        return run(args, root=root)
    except ModuleNotFoundError as error:
        if error.name != 'yaml':
            raise
        message = ('PyYAML is missing from the selected Python environment. Install Lynx dependencies: '
                   f'python3 -m pip install -r "{root}/tools/vpython_tools/requirements.in"')
    except (OSError, ValueError) as error:
        message = str(error)
    if '--format=json' in args or any(args[i:i+2] == ['--format', 'json'] for i in range(len(args))):
        print(json.dumps({'problems': [{'file': str(root / 'tools/cdpc.py'), 'line': 1,
                          'code': 'ENV', 'message': message, 'entry': '', 'severity': 'error'}],
                          'summary': 'cdpc: toolchain unavailable'}, indent=2))
    else:
        print('cdpc: ' + message, file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())

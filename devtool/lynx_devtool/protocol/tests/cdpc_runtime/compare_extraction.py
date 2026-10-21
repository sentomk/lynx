#!/usr/bin/env python3
# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Compare frozen legacy and pinned consumer tools against the same checkout."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

SNAPSHOT = r'''
import contextlib, io, json, sys
from pathlib import Path
from unittest.mock import patch
from cdpc import emit, inventory, model
from cdpc import __main__ as cli
repo=model.Repo(Path(sys.argv[1]).resolve())
contract,_=model.load(repo)
sources=inventory.scan(repo,contract,strict=True)
contract=inventory.complete(contract,sources)
outputs={repo.relative(p):t for p,t in emit.generate(repo,contract,sources).items()}
commands={}
for args in [('check','--strict','--format','json'),('write','--dry-run','--format','json'),
             ('show','--format','json'),('upstream','diff','--format','json')]:
    output=io.StringIO()
    with patch.object(model.Repo,'current',return_value=repo),contextlib.redirect_stdout(output):
        status=cli.main(list(args))
    commands[' '.join(args)]={'status':status,'output':json.loads(output.getvalue())}
print(json.dumps({'outputs':outputs,'commands':commands},sort_keys=True))
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy-scripts',type=Path,required=True,
                        help='Frozen directory containing the original cdpc package')
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    protocol=Path(__file__).resolve().parents[2]
    root=protocol.parents[2]
    snapshots=[]
    for scripts in (args.legacy_scripts,protocol/'scripts'):
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1',
             'PYTHONPATH':str(scripts.resolve())+os.pathsep+os.environ.get('PYTHONPATH','')}
        result=subprocess.run([sys.executable,'-c',SNAPSHOT,str(root)],env=env,
                              cwd=root,text=True,capture_output=True)
        if result.returncode:
            raise RuntimeError(result.stderr)
        snapshots.append(json.loads(result.stdout))
    if snapshots[0]!=snapshots[1]:
        for label,before in snapshots[0].items():
            for name,value in before.items():
                if value!=snapshots[1].get(label,{}).get(name):
                    print(f'mismatch: {label}: {name}',file=sys.stderr)
        return 1
    pin=json.loads((protocol/'cdpc_toolchain.json').read_text())
    report={'consumer_pin':pin,'equal':True,
            'outputs':{name:hashlib.sha256(text.encode()).hexdigest()
                       for name,text in snapshots[0]['outputs'].items()},
            'commands':{name:{'status':row['status'],'summary':row['output']['summary']}
                        for name,row in snapshots[0]['commands'].items()},
            'legacy_modules':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in sorted((args.legacy_scripts/'cdpc').glob('*.py'))}}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')
    print('Identical: three generated outputs and four CLI reports. Evidence: '+str(args.report))
    return 0


if __name__=='__main__':
    sys.exit(main())

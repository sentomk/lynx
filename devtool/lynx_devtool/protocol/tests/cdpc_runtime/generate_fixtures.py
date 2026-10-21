#!/usr/bin/env python3
# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Emit checked CDPC runtime fixtures through the Lynx C++ adapter."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

scripts = Path(__file__).resolve().parents[2] / 'scripts'
sys.path.insert(0,str(scripts))
from cdpc_toolchain import activate
source = activate()
from cdpc import model
from cdpc_core.descriptors import graph_data
from cdpc_core.emit import write
from cdpc_cpp import generate


def fixtures():
    raw=json.loads((source/'python/tests/testdata/case_contract.json').read_text())
    entries={}
    for name,item in raw['methods'].items():
        entry=model.Entry(name,'method',item['params'],item['returns'],[],'local')
        entries[entry.key]=entry
    contract=model.Contract(entries,raw['types'])
    shared,report=generate(graph_data(contract))
    assert report['omitted']==[{'name':'Types.root','reason':'returns root must be exactly object for MethodContract'}]
    # The real pinned upstream declaration supplies Input.insertText, even without a local override.
    contract,_=model.load(model.Repo.current())
    entry=contract.upstream_entries['method:Input.insertText']
    contract=replace(contract,entries={entry.key:entry})
    input_header,report=generate(graph_data(contract))
    assert not report['omitted']
    return {'tests/testdata/generated_contracts.h':shared,
            'examples/generated/input_contracts.h':input_header}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output-directory',type=Path,required=True)
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    outputs={args.output_directory/name:text for name,text in fixtures().items()}
    if args.check:
        stale=[str(p) for p,text in outputs.items() if not p.exists() or p.read_text()!=text]
        if stale:
            print('Stale generated CDPC fixtures: '+', '.join(stale),file=sys.stderr)
        return bool(stale)
    write(outputs)
    print(f'Generated {len(outputs)} runtime fixtures')
    return 0


if __name__=='__main__':
    sys.exit(main())

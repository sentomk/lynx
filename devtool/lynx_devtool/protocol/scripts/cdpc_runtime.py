# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Generate the new runtime descriptors without changing production dispatch."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import yaml
from cdpc_toolchain import activate

activate()
from cdpc import emit, inventory, lint, model
from cdpc_core import config, descriptors
from cdpc_cpp import generate


def load(repo):
    contract, _ = model.load(repo)
    sources = inventory.scan(repo, contract, strict=True)
    return inventory.complete(contract, sources), sources


def main(argv=None):
    parser = argparse.ArgumentParser(prog='tools/cdpc runtime')
    parser.add_argument('command', choices=('write','check','export'))
    parser.add_argument('targets', nargs='*')
    parser.add_argument('--output-directory', type=Path, required=True)
    parser.add_argument('--allow-unsupported', action='store_true')
    parser.add_argument('--format', choices=('text','json'), default='text')
    args = parser.parse_args(argv)
    repo = model.Repo.current()
    out = args.output_directory.resolve()
    try:
        contract, sources = load(repo)
        if args.command == 'export':
            if args.targets:
                parser.error('export always captures the complete implementation inventory')
            settings = {'version':1, 'inputs':{'root':str(repo.root),
                'docs':repo.relative(repo.docs), 'upstream':repo.relative(repo.upstream),
                'support':repo.relative(repo.support), 'inventory':str(out/'inventory.json')},
                'outputs':{'contract':'contract.json','descriptors':'descriptors.json'}}
            files = emit.write({out/'inventory.json':json.dumps(config.inventory_data(sources),indent=2)+'\n',
                                out/'cdpc.yaml':yaml.safe_dump(settings, sort_keys=False)})
            problems = sources.problems
        else:
            entries = {key:e for key,e in contract.entries.items()
                       if e.kind=='method' and model.matches(e.name,args.targets)}
            if not entries:
                raise ValueError('no methods match the targets')
            selected = replace(contract, entries=entries)
            header, report = generate(descriptors.graph_data(selected))
            outputs = {out/'cdpc_contracts.generated.h':header,
                       out/'cdpc_runtime_report.generated.json':json.dumps(report,indent=2,sort_keys=True)+'\n'}
            problems = [p for p in lint.check(repo,contract)+sources.problems
                        if not p.entry or model.matches(p.entry,args.targets)]
            for item in report['omitted']:
                problems.append(model.Diagnostic('',1,'E701',item['reason'],item['name'],
                                'warning' if args.allow_unsupported else 'error'))
            if args.command == 'write':
                files = emit.write(outputs)
            else:
                files = []
                problems += [model.Diagnostic(str(path),1,'E301','runtime descriptor is missing or stale; run tools/cdpc runtime write')
                             for path,text in outputs.items() if not path.exists() or path.read_text()!=text]
        summary = f'cdpc runtime {args.command}: {len(files)} files; {sum(p.severity=="error" for p in problems)} errors, {sum(p.severity=="warning" for p in problems)} warnings'
        if args.format == 'json':
            from dataclasses import asdict
            print(json.dumps({'problems':[asdict(p) for p in problems],'summary':summary,'files':files},indent=2))
        else:
            for p in problems:
                print(f'{p.code}: {p.severity}: {p.entry}: {p.message}')
            print(summary)
        return int(any(p.severity=='error' for p in problems))
    except (OSError,ValueError,yaml.YAMLError) as error:
        if args.format == 'json':
            print(json.dumps({'problems':[{'code':'ENV','message':str(error)}],
                              'summary':'cdpc runtime: input error'},indent=2))
        else:
            print(f'cdpc runtime: {error}',file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

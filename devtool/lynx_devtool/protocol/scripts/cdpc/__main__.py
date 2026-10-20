# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Six commands over the same contract model; no source mutations."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from cdpc import emit, inventory, lint, model


def output(problems, summary, format='text', **data):
    if format == 'json':
        print(json.dumps({'problems': [asdict(p) for p in problems], 'summary': summary, **data}, indent=2, ensure_ascii=False))
    else:
        for p in problems:
            print(f'{p.file}:{p.line}: {p.code}: ' + ('warning: ' if p.severity == 'warning' else '') + p.message)
        print(summary)
    return int(any(p.severity == 'error' for p in problems))


def parser():
    cli = argparse.ArgumentParser(prog='tools/cdpc', description='Check and inspect Lynx CDP contracts.')
    commands = cli.add_subparsers(dest='command', required=True)
    check = commands.add_parser('check', help='check contracts, examples, inventory and generated files')
    check.add_argument('targets', nargs='*')
    check.add_argument('--strict', action='store_true', help='fail when PrimJS sources are missing')
    write = commands.add_parser('write', help='regenerate the manifest and supported contract JSON')
    write.add_argument('--dry-run', action='store_true')
    show = commands.add_parser('show', help='inspect domains, entries or one effective contract')
    show.add_argument('target', nargs='?')
    for command in (check, write, show):
        command.add_argument('--format', choices=('text','json'), default='text')
    return cli


def load(repo, strict=False):
    contract, _ = model.load(repo)
    sources = inventory.scan(repo, contract, strict)
    return inventory.complete(contract, sources), sources


def show(repo, contract, sources, target, format):
    selected = [entry for entry in contract.entries.values() if model.matches(entry.name, [target] if target else [])]
    if not selected:
        return output([model.Diagnostic('', 1, 'E101', f'no entries match {target}')], 'cdpc show: no matches', format)
    if target is None:
        domains = []
        for domain in sorted({entry.name.split('.')[0] for entry in selected}):
            entries = [e for e in selected if e.name.startswith(domain + '.')]
            domains.append({'domain': domain, 'methods': sum(e.kind == 'method' for e in entries),
                            'events': sum(e.kind == 'event' for e in entries), 'origins': sorted({e.origin for e in entries})})
        if format == 'text':
            for d in domains:
                print(f'{d["domain"]:20} {d["methods"]:3} methods  {d["events"]:3} events  {", ".join(d["origins"])}')
        return output([], f'cdpc show: {len(domains)} domains', format, domains=domains)
    rows = []
    for entry in sorted(selected, key=lambda e: (e.name, e.kind)):
        registration = (sources.methods if entry.kind == 'method' else sources.events).get(entry.name)
        row = {'name': entry.name, 'kind': entry.kind, 'origin': entry.origin, 'supported': entry.supported,
               'source': repo.relative(entry.source) if entry.source else repo.relative(repo.upstream)}
        if len(selected) == 1:
            row.update(params=entry.params if entry.supported else None, returns=entry.returns if entry.supported else None,
                       examples=entry.examples, fields_from=entry.fields_from, registration=asdict(registration) if registration else None)
        rows.append(row)
        if format == 'text':
            print(f'{entry.name} ({entry.kind})  {entry.origin}' + ('' if entry.supported else '  UNSUPPORTED: see check diagnostics'))
            if len(selected) == 1:
                print(json.dumps(row, indent=2, ensure_ascii=False))
    return output([], f'cdpc show: {len(rows)} entries', format, entries=rows)


def main(argv=None):
    args = parser().parse_args(argv)
    repo = model.Repo.current()
    try:
        contract, sources = load(repo, getattr(args, 'strict', False))
        if args.command == 'show':
            return show(repo, contract, sources, args.target, args.format)
        problems = lint.check(repo, contract)
        outputs = emit.generate(repo, contract, sources)
        if args.command == 'write':
            # Invalid entries stay in the source documents and diagnostics, and are
            # excluded as whole entries. Never publish an invented `any` fallback.
            updated = emit.write(outputs, args.dry_run, print_diff=args.format == 'text')
            omitted = sum(not entry.supported for entry in contract.entries.values())
            summary = f'cdpc write: {"would update" if args.dry_run else "updated"} {len(updated)} files; {omitted} unsupported entries omitted'
        else:
            problems += inventory.check(repo, contract, sources)
            problems = [p for p in problems if not p.entry or model.matches(p.entry, args.targets)]
            problems += emit.check(repo, outputs)
            summary = f'cdpc check: {sum(p.severity == "error" for p in problems)} errors, {sum(p.severity == "warning" for p in problems)} warnings'
        return output(problems, summary, args.format,
                      **({'files': updated} if args.command == 'write' else {}))
    except (OSError, ValueError, KeyError, TypeError) as error:
        output([model.Diagnostic('', 1, 'ENV', str(error))], f'cdpc {args.command}: environment or input error', args.format)
        return 2


if __name__ == '__main__':
    sys.exit(main())

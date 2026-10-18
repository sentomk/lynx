# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""cdpc: the CDP Contract command line. Run it through tools/cdpc.

This is the only module that parses arguments. Each command parses its own
options here and calls one entry point of its module.
"""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path
from typing import Callable, Optional

from cdpc import diff, inventory, outputs, query, stubs, style, upstream
from cdpc.repo import CdpcError, Paths, get_paths


USAGE = """\
usage: tools/cdpc [--color=auto|always|never] [--width=N] <command> [args]

Lynx DevTool CDP Contract: custom_cdp_docs is the source of truth for the
manifest, the runtime contract checker and generated C++.

commands:
  write [--require-primjs]          regenerate the manifest and generated C++
  check [--require-primjs]          fail if docs are invalid or outputs are stale
  list [Domain|Domain.glob ...] [--methods|--events] [--standard|--lynx]
       [--no-docs]                  list implemented methods and sent events
  show <Domain.name>                print one entry's effective contract
  stubs <Domain> [--apply]          print Backend overrides of a typed domain;
                                    --apply adds the missing ones in place
  diff [Domain ...] [--require-primjs]
                                    list how Lynx differs from upstream CDP
  upstream write|check [options]    update or check cdp_upstream/protocol.json;
                                    see `tools/cdpc upstream check --help`
  test                              run the script unit tests

Run `tools/hab sync .` first for PrimJS sources and clang-format.
CI runs `tools/cdpc check --require-primjs`.

Output marks lines with symbols, so it reads the same without color:
  + added   - missing or omitted   ~ retyped   ! ignored   ? no docs   == section
Color is on for interactive terminals; NO_COLOR or --color=never turns it off.
Lines wrap between words at 100 columns or the terminal width; --width=N sets it.
"""
PRIMJS_HELP = "fail instead of reusing manifest rows when PrimJS sources are missing"


class _Exit(Exception):
    """Stop a command with an exit status after it reported the reason."""

    def __init__(self, status: int):
        super().__init__(status)
        self.status = status


def _parser(command: str, description: str, primjs: bool = False) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"tools/cdpc {command}", description=description)
    if primjs:
        parser.add_argument("--require-primjs", action="store_true", help=PRIMJS_HELP)
    return parser


def _contracts(paths: Paths, warn: bool = False):
    contracts = inventory.load_contracts(paths)
    if warn:
        inventory.report_contract_warnings(contracts)
    if contracts.errors:
        raise _Exit(inventory.report_contract_errors(contracts))
    return contracts


def _inventory(require_primjs: bool) -> inventory.Inventory:
    paths = get_paths()
    return inventory.load_inventory(paths, _contracts(paths), require_primjs)


def cmd_write(argv: list[str]) -> int:
    options = _parser("write", "Regenerate the manifest and generated C++.", True).parse_args(argv)
    paths = get_paths()
    return outputs.write(paths, _contracts(paths, warn=True), options.require_primjs)


def cmd_check(argv: list[str]) -> int:
    options = _parser("check", "Fail if docs are invalid or outputs are stale.", True).parse_args(
        argv
    )
    paths = get_paths()
    return outputs.check(paths, _contracts(paths, warn=True), options.require_primjs)


def cmd_list(argv: list[str]) -> int:
    parser = _parser("list", "List implemented methods and sent events.", True)
    parser.add_argument("patterns", nargs="*", metavar="Domain|Domain.glob")
    kind = parser.add_mutually_exclusive_group()
    kind.add_argument("--methods", dest="kind", action="store_const", const="method")
    kind.add_argument("--events", dest="kind", action="store_const", const="event")
    origin = parser.add_mutually_exclusive_group()
    origin.add_argument("--standard", dest="origin", action="store_const", const="standard")
    origin.add_argument("--lynx", dest="origin", action="store_const", const="lynx-extension")
    parser.add_argument("--no-docs", action="store_true", help="only entries without docs")
    options = parser.parse_args(argv)
    found = _inventory(options.require_primjs)
    selected = query.select(
        query.from_inventory(found), options.patterns, options.kind, options.origin, options.no_docs
    )
    print(query.format_list(selected), end="")
    return 0


def cmd_show(argv: list[str]) -> int:
    parser = _parser("show", "Print one entry's effective contract.", True)
    parser.add_argument("name", metavar="Domain.name")
    options = parser.parse_args(argv)
    found = _inventory(options.require_primjs)
    text = query.show(
        options.name, query.from_inventory(found), found.contracts, found.pristine, found.display
    )
    if text is None:
        raise CdpcError(f"no CDP method or event named `{options.name}`")
    print(text, end="")
    return 0


def cmd_stubs(argv: list[str]) -> int:
    parser = _parser("stubs", "Print or add the Backend overrides of a typed domain.")
    parser.add_argument("domain", metavar="Domain")
    parser.add_argument(
        "--apply", action="store_true", help="add missing overrides to the Backend implementation"
    )
    options = parser.parse_args(argv)
    paths = get_paths()
    contracts = _contracts(paths)
    if options.apply:
        print(stubs.apply_to_domain(paths, contracts, options.domain), end="")
    else:
        print(stubs.declarations(contracts, options.domain), end="")
    return 0


def cmd_diff(argv: list[str]) -> int:
    parser = _parser("diff", "List how Lynx differs from upstream CDP.", True)
    parser.add_argument("domains", nargs="*", metavar="Domain")
    options = parser.parse_args(argv)
    print(diff.run(_inventory(options.require_primjs), options.domains), end="")
    return 0


def cmd_upstream(argv: list[str]) -> int:
    parser = _parser("upstream", "Update or check cdp_upstream/protocol.json.")
    parser.add_argument("mode", choices=("write", "check"))
    parser.add_argument(
        "--revision",
        help="devtools-protocol git revision to fetch from GitHub. Do not use this in normal CI.",
    )
    parser.add_argument(
        "--browser-protocol", help="local path or URL for upstream json/browser_protocol.json"
    )
    parser.add_argument("--js-protocol", help="local path or URL for upstream json/js_protocol.json")
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="URL fetch timeout in seconds. Defaults to 30."
    )
    options = parser.parse_args(argv)
    local = bool(options.browser_protocol or options.js_protocol)
    if options.revision and local:
        parser.error("--revision cannot be combined with --browser-protocol or --js-protocol")
    if local and not (options.browser_protocol and options.js_protocol):
        parser.error("--browser-protocol and --js-protocol must be provided together")
    if options.mode == "write" and not options.revision and not local:
        parser.error("write requires --revision or both local upstream protocol files")
    if options.timeout <= 0:
        parser.error("--timeout must be positive")
    return upstream.run(
        get_paths(),
        options.mode == "write",
        options.revision,
        options.browser_protocol,
        options.js_protocol,
        options.timeout,
    )


def cmd_test(argv: list[str]) -> int:
    parser = _parser("test", "Run the cdpc unit tests.")
    parser.add_argument("-v", "--verbose", action="store_true")
    options = parser.parse_args(argv)
    package = Path(__file__).resolve().parent
    suite = unittest.defaultTestLoader.discover(
        str(package / "tests"), pattern="*_test.py", top_level_dir=str(package.parent)
    )
    result = unittest.TextTestRunner(verbosity=2 if options.verbose else 1).run(suite)
    return 0 if result.wasSuccessful() else 1


COMMANDS: dict[str, Callable[[list[str]], int]] = {
    "write": cmd_write,
    "check": cmd_check,
    "list": cmd_list,
    "show": cmd_show,
    "stubs": cmd_stubs,
    "diff": cmd_diff,
    "upstream": cmd_upstream,
    "test": cmd_test,
}


def global_options(argv: list[str]) -> tuple[str, Optional[int], list[str]]:
    """Take `--color[=MODE]`, `--no-color` and `--width[=N]` from anywhere."""

    mode, width, rest = "auto", None, []
    args = iter(argv)
    for arg in args:
        if arg == "--no-color":
            mode = "never"
        elif arg.startswith("--color="):
            mode = arg.split("=", 1)[1]
        elif arg == "--color":
            mode = next(args, "always")
        elif arg.startswith("--width=") or arg == "--width":
            value = arg.split("=", 1)[1] if "=" in arg else next(args, "")
            if not value.isdigit():
                raise ValueError("--width needs a number of columns")
            width = int(value)
        else:
            rest.append(arg)
    if mode not in style.MODES:
        raise ValueError(f"--color must be one of {', '.join(style.MODES)}")
    return mode, width, rest


def main(argv: Optional[list[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        mode, width, argv = global_options(argv)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    style.configure(mode, width=width)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE, end="")
        return 0 if argv else 2
    command = COMMANDS.get(argv[0])
    if command is None:
        print(f"{style.error(f'unknown command `{argv[0]}`')}\n\n{USAGE}", file=sys.stderr)
        return 2
    try:
        return command(argv[1:])
    except _Exit as exc:
        return exc.status
    except CdpcError as exc:
        print(style.error(str(exc)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

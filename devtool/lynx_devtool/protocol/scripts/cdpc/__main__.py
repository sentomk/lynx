# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.


"""Contract-only CLI during the staged model rewrite."""
import argparse
import sys
from cdpc import inventory, outputs
from cdpc.repo import CdpcError, EnvError, get_paths

def main(argv=None):
    parser = argparse.ArgumentParser(prog="tools/cdpc")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("write", "check"):
        command = commands.add_parser(name)
        command.add_argument("--require-primjs", action="store_true")
        if name == "write":
            command.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        paths = get_paths()
        contracts = inventory.load_contracts(paths)
        if args.command == "write":
            return outputs.write(paths, contracts, args.require_primjs, args.dry_run)
        return outputs.check(paths, contracts, args.require_primjs)
    except EnvError as error:
        print(error, file=sys.stderr)
        return 2
    except CdpcError as error:
        print(error, file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())

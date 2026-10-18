# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Keeps cdpc modules depending only downward; see cdpc/__init__.py."""

import ast
import unittest
from pathlib import Path

from cdpc import LAYERS

PACKAGE = Path(__file__).resolve().parents[1]


def imported_modules(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module == "cdpc":
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("cdpc."):
            found.add(node.module.split(".")[1])
    return found - {"tests"}


class LayeringTest(unittest.TestCase):
    def test_every_module_has_a_layer(self):
        modules = {path.stem for path in PACKAGE.glob("*.py")} - {"__init__"}
        listed = {module for layer in LAYERS.values() for module in layer}
        self.assertEqual(modules, listed)

    def test_modules_import_only_earlier_modules(self):
        order = [module for layer in LAYERS.values() for module in layer]
        for module in order:
            for dependency in imported_modules(PACKAGE / f"{module}.py"):
                self.assertLess(
                    order.index(dependency),
                    order.index(module),
                    f"{module} imports {dependency}, which comes later in cdpc.LAYERS",
                )

    def test_commands_do_not_import_each_other(self):
        commands = set(LAYERS["commands"])
        for module in commands:
            shared = imported_modules(PACKAGE / f"{module}.py") & commands
            self.assertFalse(shared, f"command module {module} imports {shared}")


if __name__ == "__main__":
    unittest.main()

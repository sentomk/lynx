# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""cdpc: the Lynx DevTool CDP Contract tool. Commands are in __main__.py.

A module may import only modules listed before it in LAYERS, and command
modules never import each other; tests/layering_test.py enforces both.
"""

LAYERS = {
    "base": ["repo", "style", "contract"],
    "data": ["scan", "manifest", "inventory", "outputs"],
    "commands": ["upstream"],
    "entry": ["__main__"],
}

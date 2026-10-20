# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Contract model, checks and CLI. Module imports only point downward."""
LAYERS = {
    'base': ['schema', 'model'],
    'data': ['lint', 'inventory', 'emit'],
    'commands': ['trace'],
    'entry': ['__main__'],
}

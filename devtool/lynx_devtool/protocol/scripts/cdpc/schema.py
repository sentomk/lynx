# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Compatibility imports from the pinned independent CDPC core."""
from cdpc_toolchain import activate

activate()
from cdpc_core.schema import Schema, KINDS, kinds, params_value, example_request, Problem, check, references  # noqa: E402,F401

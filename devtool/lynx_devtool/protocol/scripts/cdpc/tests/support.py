# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Shared fixtures for cdpc tests."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from cdpc import repo


class TempRepoTestCase(unittest.TestCase):
    """A temporary source root with an empty domain-agent dir and a PrimJS map."""

    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name).resolve()
        agents = root / "agents"
        agents.mkdir()
        primjs = root / "protocols.cc"
        primjs.write_text(
            'const debug_function_type& GetDebugFunctionMap() {\n'
            '  return {{"Runtime.enable", Enable}};\n}\n',
            encoding="utf-8",
        )
        self.paths = replace(
            repo.get_paths(),
            source_root=root,
            domain_agent_dir=agents,
            primjs_protocols_source=primjs,
        )


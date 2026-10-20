# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Tests for outputs.py and schema_cpp.py."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from cdpc import outputs, repo
from cdpc.tests.support import TempRepoTestCase


class ConclusionTest(unittest.TestCase):
    def test_conclusion_line_reports_updates_and_errors(self):
        errors = [
            outputs.Finding("legacy", "legacy leftovers in GlobalProps", "detail"),
            outputs.Finding("contract", "TODO(cdpc) in replace.yaml", "detail"),
        ]
        self.assertEqual(
            outputs.conclusion_line("write", 3, errors, []),
            "✗ write: updated 3 files; "
            "2 errors (legacy leftovers in GlobalProps, TODO(cdpc) in replace.yaml)",
        )
        self.assertEqual(outputs.conclusion_line("write", 0, [], []), "✓ write: up to date")
        self.assertEqual(
            outputs.conclusion_line("check", 0, [], [outputs.Finding("advisory", "x", "x")]),
            "✓ check: up to date; 1 warning",
        )

    def test_finish_prints_one_conclusion(self):
        report = outputs.Report("check")
        report.warn("advisory", "a warning", "a warning")
        report.error("contract", "TODO(cdpc) in replace.yaml", "replace.yaml:1: TODO(cdpc)")
        from io import StringIO
        import contextlib

        buffer = StringIO()
        with contextlib.redirect_stdout(buffer):
            status = report.finish()
        text = buffer.getvalue()
        self.assertEqual(status, 1)
        self.assertTrue(text.rstrip().endswith(
            "✗ check: 1 error (TODO(cdpc) in replace.yaml)"
        ))
        self.assertEqual(text.count("✗ check:"), 1)
        self.assertLess(text.index("warning:"), text.index("error:"))
        self.assertLess(text.index("error:"), text.index("✗ check:"))


class RelativePathTest(TempRepoTestCase):
    def test_symlink_outside_the_repo_is_an_env_error(self):
        outside = Path(tempfile.mkdtemp(prefix="cdpc-outside-"))
        self.addCleanup(lambda: outside.rmdir() if outside.exists() else None)
        link = self.paths.source_root / "linked-src"
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(repo.EnvError) as caught:
            repo.relative_path(self.paths, link / "protocols.cc")
        self.assertIn("symlink", str(caught.exception))
        self.assertIsInstance(caught.exception, repo.CdpcError)


if __name__ == "__main__":
    unittest.main()

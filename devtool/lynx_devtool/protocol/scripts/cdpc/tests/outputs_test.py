# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Tests for outputs.py and schema_cpp.py."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from cdpc import contract, outputs, repo, schema_cpp
from cdpc.tests.support import TempRepoTestCase


class OutputsTest(TempRepoTestCase):
    def test_write_if_changed_keeps_identical_file(self):
        target = self.paths.source_root / "out.yaml"
        target.write_text("same\n", encoding="utf-8")
        before = target.stat().st_mtime_ns
        outputs.write_if_changed(target, "same\n")
        self.assertEqual(target.stat().st_mtime_ns, before)
        outputs.write_if_changed(target, "new\n")
        self.assertEqual(target.read_text(encoding="utf-8"), "new\n")

    def test_cpp_string_literals_escape_and_wrap(self):
        text = 'a"b\\c?' * 20
        pieces = schema_cpp.cpp_string_literals(text, 12)
        self.assertTrue(all(len(piece) <= 14 for piece in pieces))
        decoded = "".join(
            piece[1:-1].replace('\\"', '"').replace("\\?", "?").replace("\\\\", "\\")
            for piece in pieces
        )
        self.assertEqual(decoded, text)

    def test_generated_cpp_embeds_schema_in_chunks(self):
        contracts = contract.ContractSet(
            methods={}, events={}, deviations=[], errors=[], warnings=[]
        )
        self.paths = replace(
            self.paths,
            generated_header=self.paths.source_root / "gen/cdp_protocol.generated.h",
            generated_source=self.paths.source_root / "gen/cdp_protocol.generated.cc",
            schema_header=self.paths.source_root / "gen/schema.generated.h",
            schema_source=self.paths.source_root / "gen/schema.generated.cc",
            fixture_dir=self.paths.source_root / "no-fixture",
        )
        generated = outputs.build_generated_cpp(
            self.paths, contracts, [], set(), format_cpp=False
        )
        header = generated[self.paths.schema_header]
        source = generated[self.paths.schema_source]
        self.assertIn("#ifndef GEN_SCHEMA_GENERATED_H_\n", header)
        self.assertIn("std::string GetContractSchemaJson();", header)
        self.assertIn('#include "gen/schema.generated.h"', source)
        self.assertIn(
            '#include "gen/cdp_protocol.generated.h"',
            generated[self.paths.generated_source],
        )
        self.assertIn(
            '    "{\\"types\\":{},\\"commands\\":{},\\"events\\":{}}",\n', source
        )
        self.assertTrue(all(len(line) <= 80 for line in source.splitlines()))


class ConclusionTest(unittest.TestCase):
    def test_conclusion_line_reports_updates_and_errors(self):
        errors = [
            outputs.Finding("legacy", "legacy leftovers in GlobalProps", "detail"),
            outputs.Finding("contract", "TODO(cdpc) in replace.yaml", "detail"),
        ]
        self.assertEqual(
            outputs.conclusion_line("write", 3, errors, []),
            "✗ write: updated 3 files; files written; migration incomplete; "
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

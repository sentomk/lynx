# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Tests for scan.py."""

import tempfile
import unittest
from pathlib import Path

from cdpc import manifest, scan
from cdpc.repo import CdpcError
from cdpc.tests.support import TempRepoTestCase


class ScanTest(TempRepoTestCase):
    def test_scan_map_and_direct_dispatch_with_source_and_deduplication(self):
        (self.paths.domain_agent_dir / "sample.cc").write_text(
            'functions_map_["Sample.mapped"] = &SampleAgent::Mapped;\n'
            'void SampleAgent::CallMethod(const Json::Value& message) {\n'
            '  if (method == "Sample.direct") { Direct(); }\n'
            '  else if (\n method == "Sample.mapped"\n) { Mapped(); }\n'
            '  else if (method == "Runtime.forwarded") { Forward(); }\n'
            '}\n'
            'void SampleAgent::HandleNotification() {\n'
            '  if (method == "Sample.notification") { Notify(); }\n'
            '}\n',
            encoding="utf-8",
        )
        (self.paths.domain_agent_dir / "sample_unittest.cc").write_text(
            'void TestAgent::CallMethod() {\n'
            '  if (method == "Sample.testOnly") { Test(); }\n'
            '}\n',
            encoding="utf-8",
        )

        self.assertEqual(
            scan.scan_local_methods(self.paths),
            [
                scan.LocalMethod("Runtime", "enable", "protocols.cc"),
                scan.LocalMethod("Sample", "direct", "agents/sample.cc"),
                scan.LocalMethod("Sample", "mapped", "agents/sample.cc"),
            ],
        )

    def test_reject_duplicate_map_and_direct_dispatch_in_different_files(self):
        (self.paths.domain_agent_dir / "mapped.cc").write_text(
            'functions_map_["Sample.method"] = &SampleAgent::Method;\n',
            encoding="utf-8",
        )
        (self.paths.domain_agent_dir / "direct.cc").write_text(
            'void SampleAgent::CallMethod() {\n'
            '  if (method == "Sample.method") { Method(); }\n'
            '}\n',
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            CdpcError,
            r"duplicate method declaration for Sample\.method",
        ):
            scan.scan_local_methods(self.paths)

    def test_scan_current_lynx_version(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config.h"
            config.write_text(
                "#define LYNX_VERSION tasm::V_4_2  // need updated when release lynx\n",
                encoding="utf-8",
            )

            self.assertEqual(
                scan.scan_current_lynx_version(config), "4.2"
            )

    def test_missing_primjs_reuses_manifest_rows_unless_required(self):
        self.paths.primjs_protocols_source.unlink()
        existing_manifest = manifest.ExistingManifest(
            domains={"Runtime", "DOM"},
            methods={("Runtime", "enable"), ("DOM", "enable")},
            events=set(),
            method_since={},
            event_since={},
            method_source={
                ("Runtime", "enable"): "protocols.cc",
                ("DOM", "enable"): "dom.cc",
            },
        )

        self.assertEqual(
            scan.scan_local_methods(
                self.paths, existing_manifest.method_source, require_primjs=False
            ),
            [scan.LocalMethod("Runtime", "enable", "protocols.cc")],
        )
        with self.assertRaisesRegex(
            CdpcError, "missing PrimJS CDP method map"
        ):
            scan.scan_local_methods(
                self.paths, existing_manifest.method_source, require_primjs=True
            )


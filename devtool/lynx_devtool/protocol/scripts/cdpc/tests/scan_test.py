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


class InferenceTest(unittest.TestCase):
    def test_infers_order_thread_and_object_error_from_a_global_props_snippet(self):
        agent = """
        InspectorGlobalPropsAgent::InspectorGlobalPropsAgent() {
          functions_map_["GlobalProps.enable"] = &InspectorGlobalPropsAgent::Enable;
          functions_map_["GlobalProps.disable"] = &InspectorGlobalPropsAgent::Disable;
          functions_map_["GlobalProps.get"] = &InspectorGlobalPropsAgent::Get;
          functions_map_["GlobalProps.replace"] = &InspectorGlobalPropsAgent::Replace;
        }
        void InspectorGlobalPropsAgent::Replace() {
          devtool_mediator_->GlobalPropsReplace(responder, params);
        }
        void InspectorGlobalPropsAgent::Enable() {
          devtool_mediator_->GlobalPropsEnable(responder, params);
        }
        """
        mediator = """
        void LynxDevToolMediator::GlobalPropsReplace() {
          if (!RunOnTASMThread([executor] { executor->GlobalPropsReplace(responder, params); })) {}
        }
        void LynxDevToolMediator::GlobalPropsEnable() {
          if (!RunOnTASMThread([executor] { executor->GlobalPropsEnable(responder, params); })) {}
        }
        """
        executor = """
        void InspectorTasmExecutor::GlobalPropsReplace() {
          const Json::Value& global_props = params["globalProps"];
          if (!global_props.isObject()) {
            responder->SendError(CDPErrorCode::InvalidParams, "globalProps must be an object");
            return;
          }
        }
        void InspectorTasmExecutor::GlobalPropsEnable() {}
        """
        found = scan.infer_legacy_domain(
            "GlobalProps", agent, {"mediator": mediator, "executor": executor}
        )
        self.assertEqual(found.order, ["enable", "disable", "get", "replace"])
        replace = found.methods[-1]
        self.assertEqual(replace.thread, "tasm")
        self.assertEqual(replace.validate, "handler")
        self.assertEqual(replace.mediator_method, "GlobalPropsReplace")
        self.assertEqual(replace.params[0].name, "globalProps")
        self.assertEqual(replace.params[0].type_name, "object")
        self.assertEqual(replace.errors[0].message, "globalProps must be an object")
        self.assertEqual(replace.errors[0].code, "InvalidParams")

    def test_unknown_param_type_is_a_todo(self):
        agent = """
        void Agent::Agent() { functions_map_["Box.set"] = &Agent::Set; }
        void Agent::Set() { auto value = params["payload"]; }
        """
        found = scan.infer_legacy_domain("Box", agent, {})
        self.assertEqual(found.methods[0].params[0].todo, "type not recognized from asXxx/isObject")

    def test_removed_declarations_and_their_callers(self):
        diff = """\
--- a/executor.h
+++ b/executor.h
@@
-  DECLARE_DEVTOOL_CDP_METHOD(GlobalPropsReplace);
   void GlobalPropsChanged();
-  DECLARE_DEVTOOL_CDP_METHOD(GlobalPropsEnable);
"""
        self.assertEqual(
            scan.parse_removed_declarations(diff),
            ["GlobalPropsReplace", "GlobalPropsEnable"],
        )
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        source = root / "executor_unittest.cc"
        source.write_text(
            "  element_executor_->GlobalPropsReplace(responder, params);\n"
            "  // GlobalPropsEnable(ignored);\n"
            "  DECLARE_DEVTOOL_CDP_METHOD(GlobalPropsEnable);\n"
            "  element_executor_->GlobalPropsChanged();\n",
            encoding="utf-8",
        )
        callers = scan.find_removed_callers(
            root, ["GlobalPropsReplace", "GlobalPropsEnable"], lambda path: path.name
        )
        self.assertEqual(callers, [f"{source.name}:1: calls removed GlobalPropsReplace"])
        (root / "still.h").write_text(
            "  DECLARE_DEVTOOL_CDP_METHOD(GlobalPropsEnable);\n"
            "  void GetSharedData(protocol::white_board::GetSharedDataResponder responder) override;\n"
            "  executor->GlobalPropsReplace(responder, params);\n",
            encoding="utf-8",
        )
        self.assertEqual(
            scan.declarations_still_present(
                root, ["GlobalPropsReplace", "GlobalPropsEnable", "GetSharedData"]
            ),
            {"GlobalPropsEnable", "GetSharedData"},
        )


if __name__ == "__main__":
    unittest.main()

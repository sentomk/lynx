# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

import tempfile
import textwrap
import unittest
from pathlib import Path

from cdpc import repo, scan, stubs
from cdpc.codegen import BackendMethod
from cdpc.repo import CdpcError
from cdpc.tests import contract_test


HEADER = """\
class SampleAgent : public protocol::sample::Backend {
 public:
  explicit SampleAgent(int value);
  ~SampleAgent() override = default;

  void Enable(protocol::sample::EnableResponder responder) override;
  void Removed(protocol::sample::RemovedResponder responder) override;

 private:
  void Helper();
};
"""

SOURCE = """\
namespace lynx {
namespace devtool {

void SampleAgent::Enable(protocol::sample::EnableResponder responder) {
  responder.SendSuccess();
}

}  // namespace devtool
}  // namespace lynx
"""

METHODS = [
    BackendMethod("Sample.enable", "Enable", "protocol::sample::EnableResponder responder"),
    BackendMethod(
        "Sample.getValue",
        "GetValue",
        "protocol::sample::GetValueResponder responder, protocol::sample::GetValueParams params",
    ),
]


class StubsTest(unittest.TestCase):
    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.root = Path(temp_dir.name)
        self.header = self.root / "agent" / "sample_agent.h"
        self.header.parent.mkdir()
        self.header.write_text(HEADER, encoding="utf-8")
        self.source = self.header.with_suffix(".cc")
        self.source.write_text(SOURCE, encoding="utf-8")

    def test_finds_the_only_backend_implementation(self):
        self.assertEqual(
            scan.find_backend_class([self.root], "protocol::sample"),
            (self.header, "SampleAgent"),
        )
        with self.assertRaisesRegex(CdpcError, "found none"):
            scan.find_backend_class([self.root], "protocol::other")
        (self.root / "agent" / "copy.h").write_text(
            "class Copy final : public protocol::sample::Backend {};", encoding="utf-8"
        )
        with self.assertRaisesRegex(CdpcError, "exactly one"):
            scan.find_backend_class([self.root], "protocol::sample")

    def test_reports_missing_methods_without_writing(self):
        before = self.header.read_text(encoding="utf-8")
        self.assertEqual(
            [m.key for m in scan.missing_methods(self.header, "SampleAgent", METHODS)],
            ["Sample.getValue"],
        )
        self.assertEqual(self.header.read_text(encoding="utf-8"), before)

    def test_finds_classes_for_several_namespaces_in_one_scan(self):
        found = scan.find_backend_classes(
            [self.root], ["protocol::sample", "protocol::other"]
        )
        self.assertEqual(found["protocol::sample"], [(self.header, "SampleAgent")])
        self.assertEqual(found["protocol::other"], [])

    def test_adds_only_missing_methods(self):
        result = stubs.apply(self.header, "SampleAgent", METHODS)
        self.assertEqual(result.added, ["Sample.getValue"])
        self.assertEqual(result.stale, ["Removed"])

        header = self.header.read_text(encoding="utf-8")
        # Inserted after the last method the Backend still has.
        self.assertIn(
            "  void Enable(protocol::sample::EnableResponder responder) override;\n"
            "  void GetValue(protocol::sample::GetValueResponder responder, "
            "protocol::sample::GetValueParams params) override;\n"
            "  void Removed(",
            header,
        )
        source = self.source.read_text(encoding="utf-8")
        self.assertIn(
            textwrap.dedent(
                """\
                void SampleAgent::GetValue(protocol::sample::GetValueResponder responder, protocol::sample::GetValueParams params) {
                  responder.SendError(CDPErrorCode::MethodNotFound,
                                      "Sample.getValue is not implemented");
                }

                }  // namespace devtool
                """
            ),
            source,
        )
        self.assertTrue(source.startswith(SOURCE.split("}  // namespace devtool")[0]))

        # A second run changes nothing.
        again = stubs.apply(self.header, "SampleAgent", METHODS)
        self.assertEqual(again.added, [])
        self.assertEqual(self.header.read_text(encoding="utf-8"), header)
        self.assertEqual(self.source.read_text(encoding="utf-8"), source)

    def test_appends_to_the_public_section_without_overrides(self):
        self.header.write_text(
            "class SampleAgent : public protocol::sample::Backend {\n"
            " public:\n"
            "  explicit SampleAgent(int value);\n"
            "\n"
            " private:\n"
            "  int value_;\n"
            "};\n",
            encoding="utf-8",
        )
        stubs.apply(self.header, "SampleAgent", METHODS[:1])
        self.assertIn(
            "  explicit SampleAgent(int value);\n"
            "  void Enable(protocol::sample::EnableResponder responder) override;\n"
            "\n private:",
            self.header.read_text(encoding="utf-8"),
        )

    def test_qualifies_the_error_code_outside_lynx_devtool(self):
        self.source.write_text("namespace other {\n\n}  // namespace other\n", encoding="utf-8")
        stubs.apply(self.header, "SampleAgent", METHODS)
        self.assertIn(
            "lynx::devtool::CDPErrorCode::MethodNotFound",
            self.source.read_text(encoding="utf-8"),
        )

    def test_requires_the_source_file(self):
        self.source.unlink()
        with self.assertRaisesRegex(stubs.StubError, "missing sample_agent.cc"):
            stubs.apply(self.header, "SampleAgent", METHODS)


if __name__ == "__main__":
    unittest.main()


class ScaffoldTest(unittest.TestCase):
    """`stubs --apply` scaffolds a Backend implementation with the license header."""

    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        self.agent_dir = root / "devtool" / "lynx_devtool" / "agent" / "domain_agent"
        self.agent_dir.mkdir(parents=True)

        class FakePaths:
            source_root = root
            domain_agent_dir = self.agent_dir

        self.paths = FakePaths()

    def test_scaffold_starts_with_license_header_and_takes_stubs(self):
        header, class_name = stubs.scaffold(self.paths, "Sample", "sample")
        source = header.with_suffix(".cc")
        expected = "\n".join(repo.license_header("//")) + "\n\n"
        self.assertEqual(class_name, "InspectorSampleAgent")
        self.assertTrue(header.read_text(encoding="utf-8").startswith(expected))
        self.assertTrue(source.read_text(encoding="utf-8").startswith(expected))
        self.assertIn(
            "#ifndef DEVTOOL_LYNX_DEVTOOL_AGENT_DOMAIN_AGENT_INSPECTOR_SAMPLE_AGENT_H_",
            header.read_text(encoding="utf-8"),
        )
        self.assertEqual(
            scan.find_backend_classes([self.paths.source_root], ["protocol::sample"]),
            {"protocol::sample": [(header, class_name)]},
        )

        result = stubs.apply(header, class_name, METHODS)
        self.assertEqual(result.added, ["Sample.enable", "Sample.getValue"])
        self.assertIn("override;", header.read_text(encoding="utf-8"))
        self.assertIn("is not implemented", source.read_text(encoding="utf-8"))

    def test_scaffold_refuses_to_overwrite(self):
        (self.agent_dir / "inspector_sample_agent.h").write_text("// mine\n", encoding="utf-8")
        with self.assertRaises(CdpcError):
            stubs.scaffold(self.paths, "Sample", "sample")
        self.assertEqual(
            (self.agent_dir / "inspector_sample_agent.h").read_text(encoding="utf-8"),
            "// mine\n",
        )


class MigrationTest(contract_test.ContractTestBase):
    """stubs --apply state A writes an adapter; state C deletes the domain legacy block."""

    def test_state_a_forwards_and_state_c_removes_the_legacy_block(self):
        self.support.write_text(
            "Board:\n  codegen: typed\n  validation: legacy\n"
            "  legacy:\n"
            "    agent: devtool/lynx_devtool/agent/domain_agent/inspector_board_agent.cc\n"
            "    order: [enable]\n    migrated: []\n",
            encoding="utf-8",
        )
        self.write_doc("Board/methods/enable.yaml", "summary: Enable.\nparameters: []\n")
        agent_dir = self.root / "devtool" / "lynx_devtool" / "agent" / "domain_agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "inspector_board_agent.cc").write_text(
            "void InspectorBoardAgent::InspectorBoardAgent() {\n"
            "  functions_map_[\"Board.enable\"] = &InspectorBoardAgent::Enable;\n"
            "}\n"
            "void InspectorBoardAgent::Enable() {\n"
            "  devtool_mediator_->BoardEnable(responder, params);\n"
            "}\n",
            encoding="utf-8",
        )
        (agent_dir / "inspector_board_agent.h").write_text(
            "class InspectorBoardAgent : public CDPDomainAgentBase {\n"
            "  void Enable();\n"
            "};\n",
            encoding="utf-8",
        )
        mediator = agent_dir.parent
        (mediator / "lynx_devtool_mediator.cc").write_text(
            "void LynxDevToolMediator::BoardEnable() {\n"
            "  if (!RunOnTASMThread([] {})) {}\n"
            "}\n",
            encoding="utf-8",
        )
        (self.root / "agent" / "BUILD.gn").parent.mkdir(exist_ok=True)
        (self.root / "agent" / "BUILD.gn").write_text(
            'sources = [\n    "inspector_board_agent.cc",\n]\n',
            encoding="utf-8",
        )
        registration = self.root / "lynx_devtool_ng.cc"
        registration.write_text(
            'RegisterAgent("Board", std::make_unique<InspectorBoardAgent>(devtool_mediator_));\n',
            encoding="utf-8",
        )

        class FakePaths:
            source_root = self.root
            domain_agent_dir = agent_dir
            protocol_dir = self.root
            support_file = self.support
            registration_source = registration
            clang_format = self.root / "missing-clang-format"

        contracts = self.load()
        report = stubs.apply_to_domain(FakePaths(), contracts, "Board")
        self.assertIn("state A", report)
        adapter = (agent_dir / "inspector_board_legacy_adapter.cc").read_text(encoding="utf-8")
        self.assertIn("devtool_mediator_->BoardEnable(call.responder, call.message[\"params\"]);", adapter)
        header = (agent_dir / "inspector_board_agent.h").read_text(encoding="utf-8")
        self.assertIn("protocol::board::Backend", header)
        self.assertIn("EnableRaw", header)
        self.assertIn(
            "protocol::board::CreateAgent",
            registration.read_text(encoding="utf-8"),
        )
        self.assertIn("inspector_board_legacy_adapter.cc", (self.root / "agent" / "BUILD.gn").read_text())
        self.assertTrue((self.root / "testdata" / "golden" / "Board.golden.jsonl").exists())

        # Cover every method, then state C deletes the adapter and the legacy block.
        self.support.write_text(
            "Board:\n  codegen: typed\n  validation: legacy\n"
            "  legacy:\n"
            "    agent: devtool/lynx_devtool/agent/domain_agent/inspector_board_agent.cc\n"
            "    order: [enable]\n    migrated: [enable]\n"
            "  golden:\n    file: testdata/golden/Board.golden.jsonl\n",
            encoding="utf-8",
        )
        contracts = self.load()
        report = stubs.apply_to_domain(FakePaths(), contracts, "Board")
        self.assertIn("state C", report)
        self.assertFalse((agent_dir / "inspector_board_legacy_adapter.h").exists())
        self.assertNotIn("\n  legacy:", "\n" + self.support.read_text(encoding="utf-8"))
        self.assertIn("golden:", self.support.read_text(encoding="utf-8"))


class LicenseHeaderTest(unittest.TestCase):
    def test_generated_outputs_share_the_license_header(self):
        from cdpc import outputs

        self.assertEqual(outputs.GENERATED_CPP_NOTICE[:3], repo.license_header("//"))
        self.assertEqual(
            repo.license_header("#")[0],
            "# Copyright 2026 The Lynx Authors. All rights reserved.",
        )

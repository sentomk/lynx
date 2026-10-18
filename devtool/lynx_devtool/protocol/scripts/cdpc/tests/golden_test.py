# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Session scrubbing and static golden checks."""

import json
import tempfile
import unittest
from pathlib import Path

from cdpc import contract, golden
from cdpc.repo import EnvError


def _paths(root: Path):
    class Paths:
        source_root = root
        protocol_dir = root / "protocol"

    return Paths()


class ScrubTest(unittest.TestCase):
    def test_replaces_user_content_and_keeps_shape_and_errors(self):
        session = "\n".join([
            json.dumps({
                "id": 7,
                "method": "GlobalProps.replace",
                "params": {
                    "globalProps": {"theme": "dark", "count": 3, "ok": True, "ratio": 1.5, "missing": None},
                    "url": "https://user.example/page",
                },
            }),
            json.dumps({"id": 7, "result": {"globalProps": {"title": "Hello"}}}),
            json.dumps({"id": 8, "method": "DOM.getDocument", "params": {}}),
            json.dumps({
                "id": 7,
                "error": {"code": -32602, "message": "globalProps must be an object"},
            }),
            json.dumps({"method": "GlobalProps.changed", "params": {"pageData": {"email": "a@b.co"}}}),
        ])
        kept = golden.scrub_session(session.splitlines(), "GlobalProps")
        self.assertEqual([item.get("method", item.get("id")) for item in kept], [
            "GlobalProps.replace",
            7,
            7,
            "GlobalProps.changed",
        ])
        props = kept[0]["params"]["globalProps"]
        self.assertEqual(props, {"theme": "<string>", "count": 0, "ok": False, "ratio": 0.0, "missing": None})
        self.assertEqual(kept[0]["params"]["url"], "https://placeholder.invalid/scrubbed")
        self.assertEqual(kept[1]["result"]["globalProps"], {"title": "<string>"})
        event = next(item for item in kept if item.get("method") == "GlobalProps.changed")
        self.assertEqual(event["params"]["pageData"], {"email": "<string>"})
        # The error response shares id 7 with the kept request and is kept, but
        # the second id-7 message above is the result. Both responses match.
        errors = [item for item in kept if "error" in item]
        self.assertEqual(errors[0]["error"]["message"], "globalProps must be an object")
        self.assertEqual(errors[0]["error"]["code"], -32602)
        for item in kept:
            self.assertEqual(golden.session_leaks(item), [])

    def test_renames_url_keys_and_drops_other_domains(self):
        raw = json.dumps({"id": 1, "method": "Page.navigate", "params": {"https://secret.example": 1}})
        self.assertEqual(golden.scrub_session([raw], "GlobalProps"), [])
        kept = golden.scrub_session(
            [json.dumps({"id": 1, "method": "GlobalProps.get", "params": {"https://secret.example": 1}})],
            "GlobalProps",
        )
        self.assertEqual(kept[0]["params"], {"key1": 1})

    def test_import_refuses_a_leak_and_check_rejects_a_stored_one(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        source = root / "capture.cdp.jsonl"
        source.write_text(
            json.dumps({"id": 1, "method": "GlobalProps.get", "params": {}}) + "\n",
            encoding="utf-8",
        )
        target, count = golden.import_session(_paths(root), "GlobalProps", source)
        self.assertEqual(count, 1)
        self.assertTrue(target.name.endswith(".cdp.jsonl"))
        stored = target.read_text(encoding="utf-8")
        self.assertNotIn("example.com", stored)
        # A hand-edited session that puts an e-mail back fails check.
        target.write_text(
            json.dumps({"id": 1, "method": "GlobalProps.get", "params": {"note": "a@b.co"}}) + "\n",
            encoding="utf-8",
        )
        contracts = contract.ContractSet(
            methods={}, events={}, deviations=[], errors=[], warnings=[], domain_golden={}
        )
        errors, _warnings = golden.check_golden(_paths(root), contracts)
        self.assertTrue(any("not scrubbed" in error for error in errors), errors)


class GoldenCheckTest(unittest.TestCase):
    def test_unsorted_and_missing_matrix_cases_fail(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        golden_file = root / "protocol" / "testdata" / "golden" / "Box.golden.jsonl"
        golden_file.parent.mkdir(parents=True)
        golden_file.write_text(
            "\n".join([
                json.dumps({"case": "z", "request": {"id": 1, "method": "Box.nope"}}),
                json.dumps({"case": "a", "request": {"id": 1, "method": "Box.nope"}}),
            ]) + "\n",
            encoding="utf-8",
        )
        contracts = contract.ContractSet(
            methods={},
            events={},
            deviations=[],
            errors=[],
            warnings=[],
            domain_golden={"Box": contract.GoldenSpec(file="protocol/testdata/golden/Box.golden.jsonl")},
        )
        errors, warnings = golden.check_golden(_paths(root), contracts)
        self.assertTrue(any("sorted" in error for error in errors), errors)
        self.assertTrue(any("missing" in error for error in errors), errors)
        self.assertTrue(any("requests" in warning for warning in warnings), warnings)

    def test_render_unittest_includes_the_headers_the_harness_uses(self):
        text = golden.render_unittest("GlobalProps", "global_props", "InspectorGlobalPropsAgent", "golden.jsonl", ["params.timestamp"])
        self.assertIn("#include <algorithm>", text)
        self.assertIn("#include <future>", text)
        self.assertIn("element_executor_->GlobalPropsChanged();", text)
        self.assertNotIn('.get("runner"', text)
        self.assertIn("has no out", text)

    def test_missing_golden_executable_is_an_environment_error(self):
        with self.assertRaises(EnvError):
            golden.run_golden_bin(Path("/no/such/devtool_agent_unittest_exec"))


if __name__ == "__main__":
    unittest.main()

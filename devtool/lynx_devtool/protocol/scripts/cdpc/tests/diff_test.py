# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

import unittest

from cdpc import contract, diff, scan
from cdpc.tests.contract_test import ContractTestBase


class UpstreamDiffTest(ContractTestBase):
    def report(self, implemented=(), sent=(), domains=None):
        contracts = self.load()
        self.assertEqual(contracts.errors, [])
        pristine = contract.load_upstream(self.upstream_path)
        return diff.build_report(
            pristine,
            contracts,
            implemented,
            sent,
            lambda path: path.relative_to(self.root).as_posix(),
            domains,
        )

    def fields(self, domain):
        return {(f.entry, f.path, f.change): (f.detail, f.source) for f in domain.fields}

    def test_lists_method_and_event_inventory(self):
        self.write_doc(
            "DOM/methods/inspect.yaml",
            """\
            summary: Lynx extension.
            returns: []
            """,
        )
        [domain] = self.report(
            implemented=[("DOM", "enable"), ("DOM", "inspect")],
            sent=[("DOM", "documentUpdated")],
        )
        self.assertEqual(domain.missing_methods, ["getDocument"])
        self.assertEqual(domain.extension_methods, ["inspect"])
        self.assertEqual(domain.undocumented_methods, ["enable"])
        self.assertEqual(domain.sent_events, ["documentUpdated"])
        self.assertEqual(domain.unsent_events, ["paused"])
        self.assertEqual(domain.fields, [])

    def test_reports_deviations_and_docs_additions(self):
        self.support.write_text(
            """\
DOM:
  deviations:
    - target: paused.parameters.reason
      kind: retyped
      as: {type: string}
      reason: Engine reports custom reasons.
    - target: paused.parameters.hint
      kind: omitted
      reason: Never sent.
""",
            encoding="utf-8",
        )
        self.write_doc(
            "DOM/methods/enable.yaml",
            """\
            summary: Enables DOM.
            parameters:
              - name: useCompression
                type: boolean
                optional: true
            returns: []
            """,
        )
        [domain] = self.report(implemented=[("DOM", "enable")])
        self.assertEqual(
            self.fields(domain),
            {
                ("DOM.enable", "parameters.useCompression", "added"): (
                    "boolean, optional",
                    "custom_cdp_docs/DOM/methods/enable.yaml",
                ),
                ("DOM.paused", "parameters.reason", "retyped"): (
                    "string enum(other) -> string",
                    "cdp_support.yaml: Engine reports custom reasons.",
                ),
                ("DOM.paused", "parameters.hint", "omitted"): (
                    "string, optional",
                    "cdp_support.yaml: Never sent.",
                ),
            },
        )

    def test_reports_docs_fields_nested_in_upstream_fields(self):
        # Docs may only add top-level fields; anything nested under an
        # upstream field is dropped from the contract.
        self.write_doc(
            "DOM/methods/getDocument.yaml",
            """\
            summary: Returns the document.
            returns:
              - name: root
                type: object
              - name: root.nodeId
                type: integer
              - name: root.compress
                type: boolean
                optional: true
              - name: root.children
                type: array
                optional: true
              - name: 'root.children[].frameId'
                type: string
            """,
        )
        [domain] = self.report(implemented=[("DOM", "getDocument")])
        fields = self.fields(domain)
        self.assertEqual(
            set(fields),
            {
                ("DOM.getDocument", "returns.root.compress", "ignored"),
                ("DOM.getDocument", "returns.root.children[].frameId", "ignored"),
            },
        )
        detail, source = fields[("DOM.getDocument", "returns.root.compress", "ignored")]
        self.assertIn("inside $ref DOM.Node", detail)
        self.assertEqual(source, "custom_cdp_docs/DOM/methods/getDocument.yaml")

    def test_format_groups_by_domain(self):
        self.write_doc(
            "DOM/methods/enable.yaml",
            """\
            summary: Enables DOM.
            parameters:
              - name: useCompression
                type: boolean
                optional: true
            returns: []
            """,
        )
        text = diff.format_report(self.report(implemented=[("DOM", "enable")]))
        self.assertIn("== DOM ==", text)
        self.assertIn("  methods  1 of 2 upstream implemented", text)
        self.assertIn("    - not implemented (1): getDocument", text)
        self.assertIn(
            "    + added    enable.parameters.useCompression: boolean, optional", text
        )
        self.assertIn("-- 1 domain; field differences: + 1 added, - 0 omitted", text)
        self.assertNotIn("\033[", text)

    def test_scans_event_literals_outside_tests(self):
        upstream = contract.load_upstream(self.upstream_path)
        source = self.root / "src" / "dom_agent.cc"
        source.parent.mkdir()
        source.write_text(
            'msg["method"] = "DOM.documentUpdated"; call("DOM.enable");', encoding="utf-8"
        )
        test = self.root / "src" / "dom_agent_unittest.cc"
        test.write_text('"DOM.paused"', encoding="utf-8")
        found = scan.scan_event_references([self.root / "src"], upstream.events)
        self.assertEqual(set(found), {("DOM", "documentUpdated")})


if __name__ == "__main__":
    unittest.main()

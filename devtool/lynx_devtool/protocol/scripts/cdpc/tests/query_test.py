# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

import unittest

from cdpc import contract, query
from cdpc.tests.contract_test import ContractTestBase


class QueryTestBase(ContractTestBase):
    """A DOM contract with a deviation, a docs addition and an extension."""

    def setUp(self):
        super().setUp()
        self.support.write_text(
            """\
DOM:
  deviations:
    - target: paused.parameters.reason
      kind: retyped
      as: {type: string}
      reason: Engine reports custom reasons.
""",
            encoding="utf-8",
        )
        self.write_doc(
            "DOM/methods/enable.yaml",
            """\
            summary: Enables DOM.
            since: '4.1'
            parameters:
              - name: useCompression
                type: boolean
                optional: true
            returns: []
            """,
        )
        self.write_doc(
            "DOM/methods/inspect.yaml",
            """\
            summary: Lynx extension.
            returns:
              - name: tags
                type: array
              - name: 'tags[].name'
                type: string
            """,
        )

    def query(self):
        contracts = self.load()
        self.assertEqual(contracts.errors, [])
        pristine = contract.load_upstream(self.upstream_path)
        display = lambda path: path.relative_to(self.root).as_posix()
        entries = query.collect(
            contracts,
            pristine,
            [
                ("DOM", "enable", "dom_agent.cc"),
                ("DOM", "getDocument", "dom_agent.cc"),
                ("DOM", "inspect", "dom_agent.cc"),
            ],
            {("DOM", "documentUpdated"): "dom_agent.cc"},
            {"DOM": "instance"},
            display,
        )
        return entries, contracts, pristine, display


class QueryTest(QueryTestBase):
    def test_list_filters_entries(self):
        entries, *_ = self.query()
        self.assertEqual(
            [entry.key for entry in entries],
            ["DOM.documentUpdated", "DOM.enable", "DOM.getDocument", "DOM.inspect"],
        )
        keys = lambda **kw: [entry.key for entry in query.select(entries, **kw)]
        self.assertEqual(keys(patterns=["DOM.get*"]), ["DOM.getDocument"])
        self.assertEqual(keys(patterns=["CSS"]), [])
        self.assertEqual(keys(kind="event"), ["DOM.documentUpdated"])
        self.assertEqual(keys(origin="lynx-extension"), ["DOM.inspect"])
        self.assertEqual(
            keys(kind="method", undocumented=True), ["DOM.getDocument"]
        )
        text = query.format_list(entries)
        self.assertIn("DOM.inspect", text)
        self.assertIn("-- 3 methods, 1 events, 2 without docs", text)

    def test_show_labels_field_provenance(self):
        entries, contracts, pristine, display = self.query()
        text = query.show("DOM.enable", entries, contracts, pristine, display)
        self.assertIn("== DOM.enable ==  (method, standard, instance scope)", text)
        self.assertIn("implemented: dom_agent.cc", text)
        self.assertIn("lifecycle:   since 4.1", text)
        self.assertRegex(text, r"includeWhitespace +string enum\(none, all\), optional +upstream")
        self.assertRegex(text, r"useCompression +boolean, optional +\+ added in docs")

        text = query.show("DOM.paused", entries, contracts, pristine, display)
        self.assertIn("sent from:   not found in sources", text)
        self.assertRegex(
            text, r"reason +string +~ retyped by deviation: Engine reports custom reasons\."
        )

    def test_show_extension_and_upstream_only_entries(self):
        entries, contracts, pristine, display = self.query()
        text = query.show("DOM.inspect", entries, contracts, pristine, display)
        self.assertIn("(method, lynx-extension, instance scope)", text)
        self.assertRegex(text, r"tags\[\]\.name +string +docs")
        self.assertNotIn("upstream:", text)

        # Upstream-only entries are shown so their shape can be looked up.
        text = query.show("DOM.getDocument", entries, contracts, pristine, display)
        self.assertIn("docs:        none", text)
        self.assertIn("    DOM.Node:", text)
        self.assertIsNone(query.show("DOM.nope", entries, contracts, pristine, display))


if __name__ == "__main__":
    unittest.main()

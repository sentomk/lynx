# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from cdpc import contract


UPSTREAM = {
    "domains": [
        {
            "domain": "DOM",
            "types": [
                {
                    "id": "Node",
                    "type": "object",
                    "properties": [
                        {"name": "nodeId", "type": "integer"},
                        {"name": "children", "type": "array", "optional": True,
                         "items": {"$ref": "Node"}},
                    ],
                }
            ],
            "commands": [
                {
                    "name": "enable",
                    "parameters": [
                        {"name": "includeWhitespace", "type": "string", "optional": True,
                         "enum": ["none", "all"]}
                    ],
                },
                {
                    "name": "getDocument",
                    "parameters": [{"name": "depth", "type": "integer", "optional": True}],
                    "returns": [{"name": "root", "$ref": "Node"}],
                },
            ],
            "events": [
                {"name": "documentUpdated"},
                {
                    "name": "paused",
                    "parameters": [
                        {"name": "reason", "type": "string", "enum": ["other"]},
                        {"name": "hint", "type": "string", "optional": True},
                    ],
                },
            ],
        }
    ]
}


class ContractTestBase(unittest.TestCase):
    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.root = Path(temp_dir.name)
        self.docs = self.root / "custom_cdp_docs"
        self.support = self.root / "cdp_support.yaml"
        schema = self.root / "protocol.json"
        schema.write_text(json.dumps(UPSTREAM), encoding="utf-8")
        self.upstream_path = schema

    def write_doc(self, relative: str, text: str, examples: str = "") -> None:
        path = self.docs / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        body = textwrap.dedent(text)
        if examples:
            body += textwrap.indent(textwrap.dedent(examples), "  ")
        path.write_text(body, encoding="utf-8")

    def load(self) -> contract.ContractSet:
        upstream = contract.load_upstream(self.upstream_path)
        return contract.load_contracts(
            self.docs,
            self.support,
            upstream,
            lambda path: path.relative_to(self.root).as_posix(),
        )

    def assertErrors(self, contracts, *fragments):
        joined = "\n".join(contracts.errors)
        for fragment in fragments:
            self.assertIn(fragment, joined)
        self.assertEqual(len(contracts.errors), len(fragments), joined)


class FieldShapeTest(ContractTestBase):
    def test_nested_paths_restore_objects_arrays_and_maps(self):
        self.write_doc(
            "Sample/methods/read.yaml",
            """\
            summary: Reads entries.
            parameters:
              - name: tags
                type: array
              - name: 'tags[]'
                type: string
              - name: frame
                type: array
                optional: true
                items: {type: number}
            returns:
              - name: entries
                type: array
              - name: 'entries[].key'
                type: string
              - name: detail
                type: object
              - name: 'detail.<key>.size'
                type: integer
            """,
        )
        contracts = self.load()
        self.assertEqual(contracts.errors, [])
        entry = contracts.methods[("Sample", "read")]
        params = entry.parameters.properties
        self.assertEqual(params["tags"].schema.items.kinds, {"string"})
        self.assertTrue(params["frame"].optional)
        self.assertEqual(params["frame"].schema.items.kinds, {"number"})
        returns = entry.returns.properties
        self.assertIn("key", returns["entries"].schema.items.properties)
        self.assertIn("size", returns["detail"].schema.additional.properties)

    def test_reject_malformed_fields(self):
        self.write_doc(
            "Sample/methods/bad.yaml",
            """\
            summary: Bad fields.
            parameters:
              - name: a.b
                type: string
              - name: s
                type: string
              - name: s.child
                type: string
              - name: 'n[]'
                type: string
              - name: u
                type: any|string
              - name: e
                type: integer
                enum: [x]
              - name: arr
                type: array
              - name: 'arr[]'
                type: string
                optional: true
              - name: z
                type: strin
              - name: k
                type: string
                format: date
              - name: s
                type: string
            """,
        )
        contracts = self.load()
        self.assertErrors(
            contracts,
            "parent field of `a.b` must be declared first",
            "parent of `s.child` must include type object",
            "parent field of `n[]` must be declared first",
            "`any` cannot be part of a union",
            "`enum` must be a non-empty string list on a string field",
            "`arr[]` is an element or map value; it cannot be optional",
            "unknown type 'strin'",
            "unknown field key `format`",
            "duplicate field path `s`",
        )

    def test_errors_carry_file_and_line(self):
        self.write_doc(
            "Sample/methods/line.yaml",
            """\
            summary: Line numbers.
            parameters:
              - name: ok
                type: string
              - name: broken
                type: nope
            """,
        )
        contracts = self.load()
        self.assertEqual(
            contracts.errors,
            ["custom_cdp_docs/Sample/methods/line.yaml:5: unknown type 'nope'"],
        )

    def test_root_entry_describes_whole_value(self):
        self.write_doc(
            "Sample/methods/version.yaml",
            """\
            summary: Returns a string result.
            parameters: []
            returns:
              - name: result
                root: true
                type: string
            examples:
              - title: Version
                request: {id: 1, method: Sample.version}
                response: {id: 1, result: '1.0'}
              - title: Wrong shape
                request: {id: 2, method: Sample.version}
                response: {id: 2, result: {}}
            """,
        )
        contracts = self.load()
        self.assertErrors(contracts, "examples[1].response.result: expected string, got object")

    def test_root_entry_must_be_first_and_named(self):
        self.write_doc(
            "Sample/methods/root.yaml",
            """\
            summary: Misplaced root.
            returns:
              - name: other
                root: true
                type: object
            """,
        )
        self.assertErrors(self.load(), "`root: true` must be on the first returns entry named `result`")

    def test_non_object_root_params_warns_about_dispatcher(self):
        self.write_doc(
            "Sample/methods/raw.yaml",
            """\
            summary: Raw params.
            parameters:
              - name: params
                root: true
                type: string
            """,
        )
        contracts = self.load()
        self.assertEqual(contracts.errors, [])
        self.assertIn("DispatchCDPMessage rejects non-object params", contracts.warnings[0])

    def test_summary_and_unknown_top_level_keys(self):
        self.write_doc("Sample/methods/empty.yaml", "parameters: []\nowner: me\n")
        self.write_doc("Sample/events/changed.yaml", "summary: Changed.\nreturns: []\n")
        self.assertErrors(
            self.load(),
            "`summary` must be a non-empty string",
            "unknown top-level key `owner`",
            "unknown top-level key `returns`",
        )

    def test_since_must_be_quoted_major_minor(self):
        self.write_doc("Sample/events/added.yaml", "summary: Added.\nsince: '4.1'\n")
        self.write_doc("Sample/events/float.yaml", "summary: Float.\nsince: 4.1\n")
        self.write_doc("Sample/methods/patch.yaml", "summary: Patch.\nsince: '4.1.2'\n")
        contracts = self.load()
        self.assertEqual(contracts.events[("Sample", "added")].since, "4.1")
        self.assertIsNone(contracts.events[("Sample", "float")].since)
        self.assertErrors(
            contracts,
            "`since` must be a quoted `major.minor` string such as '4.1', got 4.1",
            "`since` must be a quoted `major.minor` string such as '4.1', got '4.1.2'",
        )


class LegacySchemaTest(ContractTestBase):
    def test_validation_defaults_to_strict_and_rejects_unknown_modes(self):
        self.support.write_text("Board:\n  cdpc: dispatch\n", encoding="utf-8")
        self.write_doc("Board/methods/get.yaml", "summary: Get.\n")
        contracts = self.load()
        self.assertEqual(contracts.errors, [])
        self.assertEqual(contract.domain_validation(contracts, "Board"), "strict")

        self.support.write_text(
            "Board:\n  cdpc: dispatch\n  validation: loose\n", encoding="utf-8"
        )
        self.assertErrors(self.load(), "validation must be one of ['legacy', 'strict']")

    def test_todo_marker_fails_the_contract(self):
        self.support.write_text(
            "Board:\n  cdpc: dispatch\n  validation: legacy\n"
            "  # TODO(cdpc): confirm registration order\n"
            "  legacy:\n    order: [get]\n",
            encoding="utf-8",
        )
        self.write_doc("Board/methods/get.yaml", "summary: Get.\n")
        self.assertErrors(self.load(), "TODO(cdpc) must be resolved")

    def test_legacy_order_must_name_each_method_once(self):
        self.support.write_text(
            "Board:\n  cdpc: dispatch\n  validation: legacy\n"
            "  legacy:\n    order: [get]\n",
            encoding="utf-8",
        )
        self.write_doc("Board/methods/get.yaml", "summary: Get.\n")
        self.write_doc("Board/methods/set.yaml", "summary: Set.\n")
        self.assertErrors(self.load(), "legacy.order must list each method once")

    def test_strict_domain_warns_about_a_leftover_legacy_block(self):
        self.support.write_text("Board:\n  cdpc: dispatch\n", encoding="utf-8")
        self.write_doc(
            "Board/methods/get.yaml",
            "summary: Get.\nlegacy:\n  validate: handler\n  thread: none\n",
        )
        contracts = self.load()
        self.assertEqual(contracts.errors, [])
        self.assertTrue(any("ignored while validation is strict" in item for item in contracts.warnings))


class ExampleValidationTest(ContractTestBase):
    def write_set_shared_data(self, examples: str) -> None:
        self.write_doc(
            "WhiteBoard/methods/setSharedData.yaml",
            """\
            summary: Writes one entry.
            parameters:
              - name: key
                type: string
              - name: value
                type: string
            returns: []
            events:
              - WhiteBoard.onSharedDataAdded
            examples:
            """,
            examples=examples,
        )
        self.write_doc(
            "WhiteBoard/events/onSharedDataAdded.yaml",
            """\
            summary: Added.
            parameters:
              - name: key
                type: string
              - name: value
                type: string
            """,
        )

    def test_value_type_mismatch_is_rejected(self):
        self.write_set_shared_data(
            """\
            - title: Numeric value
              request:
                id: 3
                method: WhiteBoard.setSharedData
                params: {key: count, value: 3}
              response: {id: 3, result: {}}
            """
        )
        self.assertErrors(
            self.load(), "examples[0].request.params.value: expected string, got integer"
        )

    def test_invalid_params_error_example_may_show_invalid_request(self):
        self.write_set_shared_data(
            """\
            - title: Numeric value is rejected
              request:
                id: 3
                method: WhiteBoard.setSharedData
                params: {key: count, value: 3}
              response:
                id: 3
                error: {code: -32602, message: Invalid params}
            """
        )
        self.assertEqual(self.load().errors, [])

    def test_request_response_and_event_envelopes(self):
        self.write_set_shared_data(
            """\
            - title: Unknown request field and mismatched id
              request:
                id: 1
                method: WhiteBoard.setSharedData
                params: {key: a, value: '1', extra: true}
              response: {id: 2, result: {}}
            - title: Legacy event example
              response:
                method: WhiteBoard.onSharedDataAdded
                params: {key: a}
            - title: Unlisted event
              event:
                method: WhiteBoard.onSharedDataRemoved
            - title: Error without code
              request: {id: 4, method: WhiteBoard.setSharedData, params: {key: a, value: '1'}}
              response: {id: 4, error: {message: failed}}
            """
        )
        self.assertErrors(
            self.load(),
            "examples[0].response.id: does not match request id",
            "examples[0].request.params.extra: unknown field",
            "examples[1].event.params.value: missing required field",
            "examples[2].event.method: WhiteBoard.onSharedDataRemoved is not listed in `events`",
            "examples[3].response.error.code: must be an integer",
        )

    def test_object_any_union_and_map_values(self):
        self.write_doc(
            "Sample/methods/shapes.yaml",
            """\
            summary: Shapes.
            returns:
              - name: obj
                type: object
              - name: anything
                type: any
              - name: either
                type: object|string
              - name: 'either.id'
                type: integer
              - name: counts
                type: object
              - name: 'counts.<key>'
                type: integer
            examples:
              - title: Valid
                request: {id: 1, method: Sample.shapes}
                response:
                  id: 1
                  result: {obj: {a: 1}, anything: 3, either: text, counts: {a: 1, b: 2}}
              - title: Invalid
                request: {id: 2, method: Sample.shapes}
                response:
                  id: 2
                  result: {obj: 1, anything: null, either: {id: x}, counts: {a: x}}
            """,
        )
        self.assertErrors(
            self.load(),
            "examples[1].response.result.obj: expected object, got integer",
            "examples[1].response.result.either.id: expected integer, got string",
            "examples[1].response.result.counts.a: expected integer, got string",
        )

    def test_missing_event_contract_is_reported_not_failed(self):
        self.write_doc(
            "Sample/methods/start.yaml",
            """\
            summary: Starts.
            events:
              - name: Sample.started
                description: Sent after start.
            examples:
              - title: Started
                event: {method: Sample.started}
            """,
        )
        contracts = self.load()
        self.assertEqual(contracts.errors, [])
        self.assertEqual(len(contracts.warnings), 2)
        self.assertIn("event Sample.started has no contract", contracts.warnings[0])


class StandardEntryTest(ContractTestBase):
    def test_standard_docs_add_fields_and_validate_against_upstream(self):
        self.write_doc(
            "DOM/methods/enable.yaml",
            """\
            summary: Enables DOM with Lynx extensions.
            parameters:
              - name: useCompression
                type: boolean
                optional: true
              - name: includeWhitespace
                type: integer
                optional: true
            examples:
              - title: Enable
                request:
                  id: 1
                  method: DOM.enable
                  params: {useCompression: true, includeWhitespace: bogus}
                response: {id: 1, result: {}}
            """,
        )
        contracts = self.load()
        self.assertEqual(contracts.methods[("DOM", "enable")].origin, "standard")
        self.assertErrors(
            contracts,
            "parameters.includeWhitespace redeclares upstream field as integer",
            "examples[0].request.params.includeWhitespace: 'bogus' is not one of ['none', 'all']",
        )

    def test_upstream_refs_are_resolved_recursively(self):
        self.write_doc(
            "DOM/methods/getDocument.yaml",
            """\
            summary: Gets the document.
            examples:
              - title: Nested node
                request: {id: 1, method: DOM.getDocument}
                response:
                  id: 1
                  result: {root: {nodeId: 1, children: [{nodeId: two}]}}
            """,
        )
        self.assertErrors(
            self.load(),
            "examples[0].response.result.root.children[0].nodeId: expected integer, got string",
        )


class DeviationTest(ContractTestBase):
    def test_deviations_change_effective_upstream_shape(self):
        self.support.write_text(
            textwrap.dedent(
                """\
                DOM:
                  deviations:
                    - target: paused.parameters.reason
                      kind: retyped
                      as: {type: string}
                      reason: Lynx reason.
                    - target: paused.parameters.extra
                      kind: added
                      as: {type: boolean, optional: true}
                      reason: Lynx flag.
                    - target: getDocument.returns.root
                      kind: omitted
                      reason: Not sent.
                """
            ),
            encoding="utf-8",
        )
        self.write_doc(
            "DOM/methods/getDocument.yaml",
            """\
            summary: Gets the document.
            events: [DOM.paused]
            examples:
              - title: No root
                request: {id: 1, method: DOM.getDocument}
                response: {id: 1, result: {}}
              - title: Lynx reason
                event: {method: DOM.paused, params: {reason: stopAtEntry, extra: true}}
            """,
        )
        contracts = self.load()
        self.assertEqual(contracts.errors, [])
        self.assertEqual([item.kind for item in contracts.deviations], ["retyped", "added", "omitted"])

    def test_reject_invalid_deviations(self):
        self.support.write_text(
            textwrap.dedent(
                """\
                DOM:
                  deviations:
                    - target: paused.parameters.reason
                      kind: added
                      as: {type: string}
                      reason: Exists already.
                    - target: paused.parameters.reason
                      kind: omitted
                      as: {type: string}
                      reason: As is forbidden.
                    - target: missing.parameters.x
                      kind: omitted
                      reason: Unknown entry.
                    - target: paused.parameters.hint
                      kind: retyped
                      as: {type: string}
                Sample:
                  deviations: []
                """
            ),
            encoding="utf-8",
        )
        self.assertErrors(
            self.load(),
            "paused.parameters.reason: added requires the field to not exist upstream",
            "`as` is required for added / retyped and forbidden for omitted",
            "DOM.missing must name exactly one upstream command or event",
            "deviation needs a non-empty `reason`",
            "Sample: deviations only apply to upstream domains",
        )



class RuntimeExportTest(ContractTestBase):
    def test_export_covers_methods_events_and_type_closure(self):
        self.support.write_text(
            "DOM:\n  deviations:\n"
            "    - target: paused.parameters.reason\n"
            "      kind: retyped\n      as: {type: string}\n      reason: Lynx.\n",
            encoding="utf-8",
        )
        self.write_doc(
            "Sample/methods/version.yaml",
            """\
            summary: Version.
            parameters:
              - name: params
                root: true
                type: object
                optional: true
            returns:
              - name: result
                root: true
                type: string
            """,
        )
        self.write_doc(
            "Sample/events/changed.yaml",
            """\
            summary: Changed.
            parameters:
              - name: key
                type: string
            """,
        )
        contracts = self.load()
        self.assertEqual(contracts.errors, [])
        schema = contract.export_runtime_schema(
            contracts,
            [("Sample", "version"), ("DOM", "getDocument"), ("DOM", "unknown")],
            {"DOM", "Sample"},
        )

        self.assertEqual(
            schema["commands"]["Sample.version"],
            {"p": {"k": ["object"]}, "r": {"k": ["string"]}, "po": 1},
        )
        self.assertEqual(
            schema["commands"]["DOM.getDocument"]["r"]["p"]["root"], {"s": {"r": "DOM.Node"}}
        )
        self.assertNotIn("DOM.unknown", schema["commands"])
        self.assertEqual(list(schema["types"]), ["DOM.Node"])
        self.assertEqual(
            schema["events"]["DOM.paused"]["p"]["p"]["reason"], {"s": {"k": ["string"]}}
        )
        self.assertEqual(
            schema["events"]["Sample.changed"]["p"]["p"]["key"], {"s": {"k": ["string"]}}
        )


class EntryResolutionTest(ContractTestBase):
    def test_lookup_does_not_select_or_claim_local_documentation(self):
        contracts = self.load()
        entry = contracts.entry("method", "DOM", "getDocument")
        self.assertFalse(entry.documented)
        self.assertEqual(entry.source, self.support)
        self.assertEqual(entry.returns.properties["root"].schema.ref, "DOM.Node")
        self.assertEqual(contracts.entries(), [])
        self.assertIsNone(contracts.entry("event", "DOM", "getDocument"))
        self.assertIsNone(contracts.entry("method", "DOM", "missing"))

    def test_selection_and_policy_changes_are_observed_without_stale_cache(self):
        contracts = self.load()
        contracts.selections["DOM"] = (["enable"], ["paused"])
        contracts.domain_modes["DOM"] = "dispatch"
        self.assertEqual(
            [entry.key for entry in contracts.entries("DOM", modes={"dispatch"})],
            ["DOM.enable"],
        )
        self.assertEqual(
            [entry.key for entry in contracts.entries("DOM", modes={"types"})],
            ["DOM.paused"],
        )
        contracts.entry_modes[("method", "DOM", "enable")] = "contract"
        self.assertEqual(contracts.entries("DOM", modes={"dispatch"}), [])
        self.assertIsNotNone(contracts.entry("method", "DOM", "enable"))
        contracts.selections.clear()
        self.assertEqual(contracts.entries(), [])

    def test_resolved_docs_keep_metadata_and_effective_upstream_fields(self):
        self.write_doc(
            "DOM/methods/enable.yaml",
            "summary: Enables DOM.\nsince: '4.1'\nparameters:\n"
            "  - {name: compressed, type: boolean, optional: true}\n"
            "examples:\n  - title: Enable.\n"
            "    request: {id: 1, method: DOM.enable, params: {compressed: true}}\n",
        )
        contracts = self.load()
        self.assertEqual(contracts.errors, [])
        entry = contracts.entry("method", "DOM", "enable")
        self.assertTrue(entry.documented)
        self.assertEqual(entry.since, "4.1")
        self.assertEqual(entry.summary, "Enables DOM.")
        self.assertEqual(len(entry.examples), 1)
        self.assertEqual(set(entry.parameters.properties), {"includeWhitespace", "compressed"})
        selected = contracts.entries("DOM", kind="method")[0]
        self.assertEqual(selected.examples, entry.examples)
        self.assertEqual(selected.since, entry.since)


if __name__ == "__main__":
    unittest.main()

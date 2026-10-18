# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

import json
import textwrap
import unittest

from cdpc import codegen, contract, stubs
from cdpc.tests import contract_test


class CodegenTest(contract_test.ContractTestBase):
    def generate(self, docs: dict[str, str], support: str = "Board:\n  codegen: typed\n"):
        self.support.write_text(support, encoding="utf-8")
        for path, text in docs.items():
            self.write_doc(path, text)
        contracts = self.load()
        self.assertEqual(contracts.errors, [])
        return codegen.generate(contracts, "gen/cdp_protocol.generated.h", [])

    def test_maps_field_shapes_to_cpp(self):
        header, source = self.generate(
            {
                "Board/methods/set.yaml": """\
                summary: Sets.
                parameters:
                  - name: key
                    type: string
                  - name: newValue
                    type: integer
                    optional: true
                  - name: mode
                    type: string
                    enum: [a, b]
                  - name: payload
                    type: object|string
                  - name: tags
                    type: array
                    items: {type: number}
                  - name: delete
                    type: boolean
                returns:
                  - name: entries
                    type: array
                  - name: 'entries[].id'
                    type: integer
                  - name: sizes
                    type: object
                  - name: 'sizes.<key>'
                    type: integer
                  - name: detail
                    type: object
                    optional: true
                  - name: extra
                    type: any
                """,
                "Board/events/changed.yaml": """\
                summary: Changed.
                parameters: []
                """,
            }
        )

        self.assertIn("namespace board {", header)
        self.assertIn(
            textwrap.dedent(
                """\
                struct SetParams {
                  std::string key{};
                  std::optional<int64_t> new_value{};
                  std::string mode{};
                  Json::Value payload{};
                  std::vector<double> tags{};
                  bool delete_{};
                };
                """
            ),
            header,
        )
        self.assertIn("struct SetResultEntriesItem {\n  int64_t id{};\n};", header)
        self.assertIn("  std::vector<SetResultEntriesItem> entries{};", header)
        self.assertIn("  std::map<std::string, int64_t> sizes{};", header)
        self.assertIn("  std::optional<Json::Value> detail{};", header)
        self.assertIn("  Json::Value extra{};", header)
        self.assertIn("struct Changed {};", header)
        self.assertIn("bool Decode(const Json::Value& params, SetParams* out, std::string* error);", header)
        self.assertIn("Json::Value Encode(const SetResult& result);", header)
        self.assertIn("Json::Value Encode(const Changed& event);", header)

        self.assertIn(
            '  if (!(cdp::CheckEnum(out->mode, path + ".mode", {"a", "b"}, error))) {',
            source,
        )
        self.assertIn(
            '  if (!(cdp::CheckKinds(out->payload, path + ".payload", '
            "cdp::kObject | cdp::kString, error))) {",
            source,
        )
        # Element structs are defined before the structs holding them.
        self.assertLess(header.index("struct SetResultEntriesItem {"), header.index("struct SetResult {"))
        self.assertIn(
            '  if (!cdp::DecodeField(in, "newValue", path, &out->new_value, error)) {', source
        )
        self.assertIn('  cdp::EncodeField(&out, "detail", value.detail);', source)
        self.assertNotIn("static Json::Value EncodeValue(const SetParams&", source)
        self.assertNotIn("static bool DecodeValue(const Json::Value& in, const std::string& path, SetResult*", source)
        self.assertIn('  message["method"] = "Board.changed";\n  return message;', source)

    def test_docs_only_changes_keep_generated_code(self):
        doc = """\
        summary: {summary}
        parameters:
          - name: key
            type: string
            description: {description}
        """
        first = self.generate({"Board/methods/get.yaml": doc.format(summary="A.", description="One.")})
        second = self.generate({"Board/methods/get.yaml": doc.format(summary="B.", description="Two.")})
        self.assertEqual(first, second)

    def test_type_name_resolves_collisions(self):
        doc = """\
        summary: Collides.
        returns:
          - name: item
            type: object
          - name: item.id
            type: integer
          - name: itemList
            type: array
          - name: 'itemList[]'
            type: object
            {type_name}
          - name: 'itemList[].id'
            type: integer
        """
        # `item` generates GetResultItem; forcing `itemList[]` to the same name
        # must fail.
        self.support.write_text("Board:\n  codegen: typed\n", encoding="utf-8")
        self.write_doc("Board/methods/get.yaml", doc.format(type_name="typeName: GetResultItem"))
        with self.assertRaisesRegex(codegen.CodegenError, "set `typeName` to disambiguate"):
            codegen.generate(self.load(), "gen/x.h", [])
        self.write_doc("Board/methods/get.yaml", doc.format(type_name="typeName: BoardListItem"))
        header, _ = codegen.generate(self.load(), "gen/x.h", [])
        self.assertIn("std::vector<BoardListItem> item_list{};", header)

    def test_rejects_unsupported_shapes(self):
        cases = {
            "root result": """\
            summary: Root.
            returns:
              - name: result
                root: true
                type: string
            """,
            "unresolved type reference Tree.Missing": """\
            summary: Ref.
            parameters:
              - name: node
                $ref: Tree.Missing
            """,
        }
        for message, doc in cases.items():
            with self.subTest(message=message):
                self.support.write_text("Board:\n  codegen: typed\n", encoding="utf-8")
                self.write_doc("Board/methods/bad.yaml", doc)
                with self.assertRaisesRegex(codegen.CodegenError, message):
                    codegen.generate(self.load(), "gen/x.h", [])

    def write_tree_upstream(self, kind_values=("leaf", "first-line")) -> None:
        self.upstream_path.write_text(
            json.dumps(
                {
                    "domains": [
                        {
                            "domain": "Tree",
                            "types": [
                                {"id": "NodeId", "type": "integer"},
                                {"id": "Kind", "type": "string", "enum": list(kind_values)},
                                {
                                    "id": "Node",
                                    "type": "object",
                                    "properties": [
                                        {"name": "nodeId", "$ref": "NodeId"},
                                        {"name": "kind", "$ref": "Kind"},
                                        {"name": "children", "type": "array", "optional": True,
                                         "items": {"$ref": "Node"}},
                                        {"name": "parent", "$ref": "Node", "optional": True},
                                        {"name": "owner", "$ref": "Other.Owner", "optional": True},
                                    ],
                                },
                                {"id": "Unused", "type": "object", "properties": []},
                            ],
                        },
                        {
                            "domain": "Other",
                            "types": [
                                {
                                    "id": "Owner",
                                    "type": "object",
                                    "properties": [
                                        {"name": "root", "$ref": "Tree.Node", "optional": True}
                                    ],
                                }
                            ],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )

    def test_refs_generate_aliases_enums_and_boxes(self):
        self.write_tree_upstream()
        header, source = self.generate(
            {
                "Board/methods/show.yaml": """\
                summary: Show.
                parameters:
                  - name: node
                    $ref: Tree.Node
                  - name: kinds
                    type: array
                    items: {$ref: Tree.Kind}
                  - name: values
                    type: array
                    items: {type: string|integer}
                """,
            }
        )
        self.assertIn("namespace tree {\n\nstruct Node;\n}", header)
        self.assertIn("using NodeId = int64_t;", header)
        self.assertIn('inline constexpr char kFirstLine[] = "first-line";', header)
        self.assertIn("using Kind = std::string;", header)
        # Self and cross-domain cycles are boxed; vectors are not.
        self.assertIn("  std::optional<std::vector<Node>> children{};", header)
        self.assertIn("  std::optional<cdp::Box<Node>> parent{};", header)
        self.assertIn("  std::optional<cdp::Box<other::Owner>> owner{};", header)
        self.assertIn("  std::optional<cdp::Box<tree::Node>> root{};", header)
        self.assertIn("  tree::Node node{};", header)
        self.assertNotIn("Unused", header)
        self.assertIn(
            '  if (!(cdp::CheckEach(out->kinds, path + ".kinds", [&](const auto& item0, '
            'const std::string& path0) { return cdp::CheckEnum(item0, path0, '
            '{"leaf", "first-line"}, error); }))) {',
            source,
        )
        self.assertIn("return cdp::CheckKinds(item0, path0, cdp::kInteger | cdp::kString, error);", source)
        # Recursive decoders are declared before they are defined.
        self.assertLess(
            source.index("static bool DecodeValue(const Json::Value& in, const std::string& path, Owner* out, std::string* error);"),
            source.index("static bool DecodeValue(const Json::Value& in, const std::string& path, Node* out, std::string* error) {"),
        )

    def test_value_cycles_without_vectors_are_boxed_and_ordered(self):
        self.write_tree_upstream()
        header, _ = self.generate(
            {
                "Board/events/moved.yaml": """\
                summary: Moved.
                parameters:
                  - name: owner
                    $ref: Other.Owner
                """,
            }
        )
        # Owner holds Node only through a Box, so either order compiles; the
        # event struct holds Owner by value and must come after it.
        self.assertLess(header.index("struct Owner {"), header.index("struct Moved {"))

    def test_enum_constants_must_be_distinct(self):
        self.write_tree_upstream(kind_values=("a-b", "a_b"))
        self.support.write_text("Board:\n  codegen: typed\n", encoding="utf-8")
        self.write_doc(
            "Board/methods/show.yaml",
            "summary: Show.\nparameters:\n  - name: kind\n    $ref: Tree.Kind\n",
        )
        with self.assertRaisesRegex(codegen.CodegenError, "map to the same constant kAB"):
            codegen.generate(self.load(), "gen/x.h", [])

    def test_support_rejects_invalid_codegen(self):
        self.support.write_text(
            "Board:\n  codegen: raw\nDOM:\n  codegen: typed\nEmpty:\n  codegen: typed\n"
            "Runtime:\n  codegen: typed\nSample:\n  methods: [a]\n",
            encoding="utf-8",
        )
        self.write_doc("Board/methods/get.yaml", "summary: Get.\n")
        self.assertErrors(
            self.load(),
            "Board: codegen must be one of ['typed']",
            "DOM: standard typed domains must list implemented upstream `methods`",
            "Empty: typed codegen needs docs under custom_cdp_docs/Empty/",
            "Runtime: engine-forwarded domains stay out of codegen",
            "Sample: `methods` needs `codegen: typed`",
        )

    def test_standard_selection_is_validated(self):
        self.support.write_text(
            "DOM:\n  codegen: typed\n  methods: [enable, missing]\n  events: [paused, paused]\n"
            "Board:\n  codegen: typed\n  events: [x]\n",
            encoding="utf-8",
        )
        self.write_doc("Board/methods/get.yaml", "summary: Get.\n")
        self.write_doc("DOM/methods/getDocument.yaml", "summary: Documented standard method.\n")
        self.assertErrors(
            self.load(),
            "DOM: unknown upstream method `missing`",
            "DOM: duplicate names in `events`",
            "Board: `events` selects upstream entries; Lynx-extension domains declare entries through docs",
            "DOM: documented standard method `getDocument` must be listed in `methods`",
        )

    def test_standard_domain_generates_selected_entries(self):
        header, source = self.generate(
            {
                "DOM/methods/getDocument.yaml": """\
                summary: Standard getDocument with a Lynx flag.
                parameters:
                  - name: compact
                    type: boolean
                    optional: true
                """,
                "DOM/methods/getDocumentWithBoxModel.yaml": """\
                summary: Lynx extension.
                returns:
                  - name: root
                    $ref: DOM.Node
                """,
            },
            support="DOM:\n  codegen: typed\n  methods: [getDocument]\n  events: [paused]\n",
        )
        contracts = self.load()
        entries = [(item.kind, item.name, item.source.name) for item in contract.typed_entries(contracts, "DOM")]
        self.assertEqual(
            entries,
            [
                ("method", "getDocument", "getDocument.yaml"),
                ("method", "getDocumentWithBoxModel", "getDocumentWithBoxModel.yaml"),
                ("event", "paused", "cdp_support.yaml"),
            ],
        )
        self.assertIn(
            "struct GetDocumentParams {\n  std::optional<int64_t> depth{};\n  std::optional<bool> compact{};\n};",
            header,
        )
        self.assertIn("struct GetDocumentResult {\n  Node root{};\n};", header)
        self.assertIn("  virtual void GetDocumentWithBoxModel(GetDocumentWithBoxModelResponder responder) = 0;", header)
        self.assertIn("Json::Value Encode(const Paused& event);", header)
        self.assertNotIn("Enable", header)
        self.assertNotIn("DocumentUpdated", header)

    def test_dispatch_layer(self):
        header, source = self.generate(
            {
                "Board/methods/set.yaml": "summary: Set.\nparameters:\n  - name: key\n    type: string\n",
                "Board/methods/get.yaml": "summary: Get.\nreturns:\n  - name: size\n    type: integer\n",
                "Board/methods/reset.yaml": "summary: Reset.\n",
            }
        )
        self.assertIn("using GetResponder = cdp::Responder<GetResult>;", header)
        self.assertIn("using ResetResponder = cdp::Responder<cdp::Empty>;", header)
        self.assertIn("  virtual void Get(GetResponder responder) = 0;", header)
        self.assertIn("  virtual void Set(SetResponder responder, SetParams params) = 0;", header)
        self.assertIn("class Agent : public CDPDomainAgentBase {", header)
        self.assertIn(
            "std::unique_ptr<CDPDomainAgentBase> CreateAgent(std::unique_ptr<Backend> backend);",
            header,
        )
        self.assertIn(
            '  if (method == "Board.set") {\n'
            "    SetParams params;\n"
            "    std::string error;\n"
            '    if (!Decode(message["params"], &params, &error)) {\n'
            "      responder->SendError(CDPErrorCode::InvalidParams, error);\n"
            "      return;\n"
            "    }\n"
            "    backend_->Set(SetResponder(responder), std::move(params));\n"
            "    return;\n"
            "  }",
            source,
        )
        self.assertIn("    backend_->Reset(ResetResponder(responder));", source)
        self.assertLess(
            source.index("PreDispatch(method, message, responder)"), source.index('"Board.get"')
        )
        self.assertIn("wasn't found", source)

    def test_backend_stubs(self):
        self.support.write_text("Board:\n  codegen: typed\n", encoding="utf-8")
        self.write_doc(
            "Board/methods/set.yaml", "summary: Set.\nparameters:\n  - name: key\n    type: string\n"
        )
        contracts = self.load()
        self.assertEqual(
            stubs.declarations(contracts, "Board"),
            "  // Board Backend overrides.\n"
            "  void Set(protocol::board::SetResponder responder, "
            "protocol::board::SetParams params) override;\n",
        )
        with self.assertRaisesRegex(
            codegen.CodegenError, "DOM is not a typed domain; typed domains: Board"
        ):
            stubs.declarations(contracts, "DOM")

    def test_snake_case(self):
        self.assertEqual(codegen.snake_case("WhiteBoard"), "white_board")
        self.assertEqual(codegen.snake_case("UITree"), "ui_tree")
        self.assertEqual(codegen.snake_case("newValue"), "new_value")
        self.assertEqual(codegen.member_name("default"), "default_")


if __name__ == "__main__":
    unittest.main()

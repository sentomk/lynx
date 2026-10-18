// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

// Compiles and exercises the code generator's fixture domain
// (testdata/fixture), which covers shapes the real typed domains do not use
// yet: $ref aliases and enums, recursive and cross-domain structs held through
// cdp::Box, and enum checks inside arrays and maps.

#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "devtool/base_devtool/native/public/cdp_responder.h"
#include "devtool/base_devtool/native/public/message_sender.h"
#include "devtool/lynx_devtool/protocol/testdata/fixture/fixture_protocol.generated.h"
#include "third_party/googletest/googletest/include/gtest/gtest.h"

namespace lynx {
namespace devtool {
namespace testing {

namespace {

namespace fixture = protocol_fixture::fixture;
namespace other = protocol_fixture::other;
namespace tree = protocol_fixture::tree;

Json::Value Parse(const std::string& text) {
  Json::Value value;
  Json::Reader reader;
  EXPECT_TRUE(reader.parse(text, value, false)) << text;
  return value;
}

constexpr char kValidParams[] = R"({
  "node": {"nodeId": 1, "kind": "branch", "attributes": {"id": "root"},
           "box": [0, 0, 10, 20],
           "children": [{"nodeId": 2, "kind": "leaf",
                         "parent": {"nodeId": 1, "kind": "branch"}}],
           "owner": {"name": "doc", "root": {"nodeId": 9, "kind": "leaf"}}},
  "kinds": ["leaf", "first-line"],
  "modes": {"fast": "on", "safe": "off"},
  "payload": "raw",
  "ignored": true
})";

std::string DecodeError(const std::string& text) {
  fixture::InspectParams params;
  std::string error;
  EXPECT_FALSE(fixture::Decode(Parse(text), &params, &error)) << text;
  return error;
}

}  // namespace

TEST(CDPCodegenFixtureTest, DecodesReferencedAndRecursiveTypes) {
  fixture::InspectParams params;
  std::string error;
  ASSERT_TRUE(fixture::Decode(Parse(kValidParams), &params, &error)) << error;

  EXPECT_EQ(params.node.node_id, 1);
  EXPECT_EQ(params.node.kind, tree::KindEnum::kBranch);
  EXPECT_EQ(*params.node.box, (tree::Quad{0, 0, 10, 20}));
  EXPECT_EQ((*params.node.attributes)["id"].asString(), "root");
  ASSERT_EQ(params.node.children->size(), 1u);
  const tree::Node& child = params.node.children->front();
  EXPECT_EQ(child.kind, tree::KindEnum::kLeaf);
  EXPECT_EQ((*child.parent)->node_id, 1);
  EXPECT_EQ((*params.node.owner)->name, "doc");
  EXPECT_EQ((*(*params.node.owner)->root)->node_id, 9);
  EXPECT_EQ(params.kinds,
            (std::vector<tree::Kind>{tree::KindEnum::kLeaf,
                                     tree::KindEnum::kFirstLine}));
  EXPECT_EQ(params.modes.at("safe"), "off");
  EXPECT_EQ(params.payload->asString(), "raw");
}

TEST(CDPCodegenFixtureTest, ReportsTheFirstMismatchWithItsPath) {
  EXPECT_EQ(DecodeError(R"({"node": {"nodeId": 1, "kind": "tree"},
                            "kinds": [], "modes": {}})"),
            "params.node.kind: 'tree' is not an allowed value");
  EXPECT_EQ(DecodeError(R"({"node": {"nodeId": 1, "kind": "leaf"},
                            "kinds": ["leaf", "x"], "modes": {}})"),
            "params.kinds[1]: 'x' is not an allowed value");
  EXPECT_EQ(DecodeError(R"({"node": {"nodeId": 1, "kind": "leaf"},
                            "kinds": [], "modes": {"fast": "maybe"}})"),
            "params.modes.fast: 'maybe' is not an allowed value");
  EXPECT_EQ(DecodeError(R"({"node": {"nodeId": 1, "kind": "leaf",
                            "children": [{"nodeId": "2", "kind": "leaf"}]},
                            "kinds": [], "modes": {}})"),
            "params.node.children[0].nodeId: expected integer");
  EXPECT_EQ(DecodeError(R"({"node": {"nodeId": 1, "kind": "leaf",
                            "attributes": []}, "kinds": [], "modes": {}})"),
            "params.node.attributes: has an unexpected type");
  EXPECT_EQ(DecodeError(R"({"node": {"nodeId": 1, "kind": "leaf"},
                            "kinds": [], "modes": {}, "payload": 3})"),
            "params.payload: has an unexpected type");
  EXPECT_EQ(DecodeError(R"({"kinds": [], "modes": {}})"),
            "params.node: missing required field");
}

TEST(CDPCodegenFixtureTest, EncodesCrossDomainCycles) {
  tree::Node root;
  root.node_id = 1;
  root.kind = tree::KindEnum::kBranch;
  tree::Node leaf;
  leaf.node_id = 2;
  leaf.kind = tree::KindEnum::kLeaf;
  leaf.parent = root;
  root.children = std::vector<tree::Node>{leaf};

  fixture::InspectResult result;
  result.owner.name = "doc";
  result.owner.root = root;
  // Non-integral values: JsonCpp compares 1.0 and 1 as different types.
  result.quads = {{0.5, 1.5, 2.5, 3.5}};
  result.ids = {1, 2};

  EXPECT_EQ(fixture::Encode(result), Parse(R"({
    "owner": {"name": "doc", "root": {"nodeId": 1, "kind": "branch",
      "children": [{"nodeId": 2, "kind": "leaf",
                    "parent": {"nodeId": 1, "kind": "branch"}}]}},
    "quads": [[0.5, 1.5, 2.5, 3.5]],
    "ids": [1, 2]
  })"));
  EXPECT_EQ(fixture::Encode(fixture::Changed{tree::KindEnum::kLeaf, {}}),
            Parse(R"({"method": "Fixture.changed",
                      "params": {"kind": "leaf"}})"));
}

TEST(CDPCodegenFixtureTest, BoxHasValueSemantics) {
  other::Owner original;
  original.root = tree::Node{};
  (*original.root)->node_id = 1;

  other::Owner copy = original;
  (*copy.root)->node_id = 2;
  EXPECT_EQ((*original.root)->node_id, 1);
  EXPECT_EQ((*copy.root)->node_id, 2);

  cdp::Box<tree::Node> moved_from(tree::Node{});
  cdp::Box<tree::Node> moved_to(std::move(moved_from));
  const cdp::Box<tree::Node>& empty = moved_from;
  EXPECT_EQ(empty->node_id, 0);
  EXPECT_FALSE(empty->owner.has_value());
}

TEST(CDPCodegenFixtureTest, AgentDecodesBeforeCallingBackend) {
  class Backend : public fixture::Backend {
   public:
    void Inspect(fixture::InspectResponder responder,
                 fixture::InspectParams params) override {
      fixture::InspectResult result;
      result.owner.name = std::to_string(params.node.node_id);
      responder.SendSuccess(result);
    }
  };
  class Sender : public MessageSender {
   public:
    void SendMessage(const std::string&, const Json::Value& msg) override {
      messages.push_back(msg);
    }
    void SendMessage(const std::string&, const std::string& msg) override {
      messages.push_back(Parse(msg));
    }
    std::vector<Json::Value> messages;
  };

  auto agent = fixture::CreateAgent(std::make_unique<Backend>());
  auto sender = std::make_shared<Sender>();
  auto call = [&](const std::string& params) {
    Json::Value message = Parse(R"({"id": 1, "method": "Fixture.inspect"})");
    message["params"] = Parse(params);
    agent->CallMethod(std::make_shared<CDPResponder>(sender, 1), message);
  };
  call(kValidParams);
  call(R"({"node": {"nodeId": 1, "kind": "leaf"}, "kinds": ["nope"],
           "modes": {}})");

  ASSERT_EQ(sender->messages.size(), 2u);
  EXPECT_EQ(sender->messages[0]["result"]["owner"]["name"], "1");
  EXPECT_EQ(sender->messages[1]["error"]["message"],
            "params.kinds[0]: 'nope' is not an allowed value");
}

TEST(CDPCodegenFixtureTest, StandardDomainGeneratesTheSelectedSubset) {
  // Tree is a standard domain: only the listed upstream entries plus its
  // Lynx-extension docs are generated, with deviations and docs additions.
  class Backend : public tree::Backend {
   public:
    void GetNode(tree::GetNodeResponder responder,
                 tree::GetNodeParams params) override {
      tree::GetNodeResult result;
      result.node.node_id = params.node_id;
      result.node.kind = tree::KindEnum::kLeaf;
      result.cached = params.with_box.value_or(false);
      responder.SendSuccess(result);
    }
    void GetNodeWithBox(tree::GetNodeWithBoxResponder responder,
                        tree::GetNodeWithBoxParams params) override {
      responder.SendSuccess(tree::GetNodeWithBoxResult{{0.5, 1.5}});
    }
  };
  class Sender : public MessageSender {
   public:
    void SendMessage(const std::string&, const Json::Value& msg) override {
      messages.push_back(msg);
    }
    void SendMessage(const std::string&, const std::string& msg) override {
      messages.push_back(Parse(msg));
    }
    std::vector<Json::Value> messages;
  };

  auto agent = tree::CreateAgent(std::make_unique<Backend>());
  auto sender = std::make_shared<Sender>();
  for (const char* request : {
           R"({"id": 1, "method": "Tree.getNode",
               "params": {"nodeId": 7, "withBox": true}})",
           R"({"id": 2, "method": "Tree.getNodeWithBox",
               "params": {"nodeId": 7}})",
           R"({"id": 3, "method": "Tree.describe", "params": {}})",
       }) {
    Json::Value message = Parse(request);
    agent->CallMethod(
        std::make_shared<CDPResponder>(sender, message["id"].asInt64()),
        message);
  }

  ASSERT_EQ(sender->messages.size(), 3u);
  EXPECT_EQ(
      sender->messages[0]["result"],
      Parse(R"({"node": {"nodeId": 7, "kind": "leaf"}, "cached": true})"));
  EXPECT_EQ(sender->messages[1]["result"], Parse(R"({"box": [0.5, 1.5]})"));
  EXPECT_EQ(sender->messages[2]["error"]["message"],
            "'Tree.describe' wasn't found");

  tree::NodeChanged event;
  event.node.node_id = 3;
  event.node.kind = tree::KindEnum::kBranch;
  EXPECT_EQ(tree::Encode(event), Parse(R"({"method": "Tree.nodeChanged",
                      "params": {"node": {"nodeId": 3, "kind": "branch"}}})"));
}

TEST(CDPCodegenFixtureTest, LegacyDispatchDefersDecodeAndForwardsRawCalls) {
  namespace migrate = protocol_fixture::migrate;

  class Backend : public migrate::Backend {
   public:
    void ResetRaw(const cdp::RawCall& call) override {
      raw_method_ = call.message["method"].asString();
      migrate::ResetResponder(call.responder).SendSuccess();
    }
    void Set(migrate::SetResponder responder,
             cdp::Deferred<migrate::SetParams> params) override {
      std::optional<migrate::SetParams> decoded = params.Get(responder);
      if (!decoded) {
        return;
      }
      saw_object_ = decoded->value.isObject();
      responder.SendSuccess();
    }
    std::string raw_method_;
    bool saw_object_ = false;
  };
  class Sender : public MessageSender {
   public:
    void SendMessage(const std::string&, const Json::Value& msg) override {
      messages.push_back(msg);
    }
    void SendMessage(const std::string&, const std::string& msg) override {
      messages.push_back(Parse(msg));
    }
    std::vector<Json::Value> messages;
  };

  auto backend = std::make_unique<Backend>();
  Backend* raw = backend.get();
  auto agent = migrate::CreateAgent(std::move(backend));
  auto sender = std::make_shared<Sender>();

  Json::Value wrong = Parse(
      R"({"id": 1, "method": "Migrate.set", "params": {"value": []}})");
  agent->CallMethod(std::make_shared<CDPResponder>(sender, 1), wrong);
  ASSERT_EQ(sender->messages.size(), 1u);
  EXPECT_EQ(sender->messages[0]["error"]["message"].asString(),
            "value must be an object");
  EXPECT_FALSE(raw->saw_object_);

  Json::Value ok = Parse(
      R"({"id": 2, "method": "Migrate.set", "params": {"value": {"a": 1}}})");
  agent->CallMethod(std::make_shared<CDPResponder>(sender, 2), ok);
  EXPECT_TRUE(raw->saw_object_);

  Json::Value forwarded =
      Parse(R"({"id": 3, "method": "Migrate.reset", "params": {"kept": true}})");
  agent->CallMethod(std::make_shared<CDPResponder>(sender, 3), forwarded);
  EXPECT_EQ(raw->raw_method_, "Migrate.reset");
}

}  // namespace testing
}  // namespace devtool
}  // namespace lynx

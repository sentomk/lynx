// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.
#include "devtool/lynx_devtool/protocol/contract_invalid_params.h"

#include <cstdio>
#include <string_view>

// The fixture exposes mediator test state before agent headers include it.
// clang-format off
#include "devtool/testing/agent/contract_agent_test.h"
// clang-format on
#include "devtool/lynx_devtool/agent/domain_agent/inspector_css_agent_ng.h"
#include "devtool/lynx_devtool/agent/domain_agent/inspector_dom_agent_ng.h"
#include "devtool/lynx_devtool/agent/domain_agent/inspector_input_agent.h"
#include "devtool/lynx_devtool/agent/domain_agent/inspector_page_agent_ng.h"

namespace lynx {
namespace devtool {
namespace protocol {
std::string_view ContractJson();
}
namespace testing {
namespace {
Json::Value ExampleValue(const Json::Value& schema, const Json::Value& types,
                         int depth = 0) {
  if (depth > 8) return Json::Value();
  if (schema.isMember("ref"))
    return ExampleValue(types[schema["ref"].asString()], types, depth + 1);
  auto type = schema["type"];
  if (type.isArray()) type = type[0];
  if (type == "string")
    return schema["enum"].empty() ? Json::Value("test") : schema["enum"][0];
  if (type == "integer" || type == "number") return 1;
  if (type == "boolean") return false;
  if (type == "array") return Json::Value(Json::arrayValue);
  if (type == "object") {
    Json::Value value(Json::objectValue);
    for (const auto& name : schema["properties"].getMemberNames()) {
      const auto& prop = schema["properties"][name];
      if (!prop.get("optional", false).asBool())
        value[name] = ExampleValue(prop["schema"], types, depth + 1);
    }
    return value;
  }
  return Json::Value();
}
class ContractInvalidParamsTest : public ContractAgentTest {
 protected:
  void CheckDomain(const std::string& domain, CDPDomainAgentBase& agent) {
    const auto text = protocol::ContractJson();
    Json::Value contract;
    ASSERT_TRUE(
        Json::Reader().parse(text.data(), text.data() + text.size(), contract));
    size_t variants = 0, methods = 0;
    for (const auto& name : contract["methods"].getMemberNames()) {
      if (name.find(domain + ".") != 0) continue;
      auto request = Request(name);
      // Explicit schema-valid seeds exercise required parameters even where
      // documentation has no example. This is test data, never device traffic.
      request["params"] =
          ExampleValue(contract["methods"][name]["params"], contract["types"]);
      if (name == "Input.synthesizeTapGesture") {
        request["params"]["duration"] = 0;
      }
      ++methods;
      ForEachInvalidParamsVariant(
          name, request, [&](const Json::Value& variant) {
            ++variants;
            harness->Expect(ContractViolation::Kind::kInvalidParams, name);
            return Dispatch(agent, variant);
          });
    }
    EXPECT_GT(variants, 0u);
    std::fprintf(stderr, "[invalid params] %s: %zu methods, %zu variants\n",
                 domain.c_str(), methods, variants);
  }
};
TEST_F(ContractInvalidParamsTest, Input) {
  InspectorInputAgent agent(mediator);
  CheckDomain("Input", agent);
}
TEST_F(ContractInvalidParamsTest, Page) {
  InspectorPageAgentNG agent(mediator);
  CheckDomain("Page", agent);
}
TEST_F(ContractInvalidParamsTest, DOM) {
  InspectorDOMAgentNG agent(mediator);
  CheckDomain("DOM", agent);
}
TEST_F(ContractInvalidParamsTest, CSS) {
  InspectorCSSAgentNG agent(mediator);
  CheckDomain("CSS", agent);
}
TEST(InvalidParamsVariantTest, IsolatesOptionalFieldsWithoutChangingSeed) {
  Json::Value request;
  request["id"] = 1;
  request["method"] = "Page.startScreencast";
  request["params"] = Json::Value(Json::objectValue);
  int count = 0;
  ForEachInvalidParamsVariant("Page.startScreencast", request,
                              [&](const Json::Value& variant) {
                                ++count;
                                EXPECT_EQ(variant["params"].size(), 1u);
                                Json::Value response;
                                response["id"] = variant["id"];
                                response["error"]["code"] = -32602;
                                return response;
                              });
  EXPECT_GT(count, 0);
  EXPECT_TRUE(request["params"].empty());
}
TEST(InvalidParamsVariantTest, UsesDocumentedExampleWhenSeedIsAbsent) {
  int count = 0;
  ForEachInvalidParamsVariant("DOM.getDocument", Json::Value(),
                              [&](const Json::Value& variant) {
                                ++count;
                                EXPECT_EQ(variant["method"], "DOM.getDocument");
                                Json::Value response;
                                response["id"] = variant["id"];
                                response["error"]["code"] = -32602;
                                return response;
                              });
  EXPECT_GT(count, 0);
}

}  // namespace
}  // namespace testing
}  // namespace devtool
}  // namespace lynx

// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.
// clang-format off
#include "devtool/testing/agent/contract_agent_test.h"
// clang-format on
#include "devtool/lynx_devtool/agent/domain_agent/inspector_css_agent_ng.h"

#include "core/renderer/dom/element_manager.h"
#include "core/renderer/tasm/react/testing/mock_painting_context.h"
#include "core/shell/testing/mock_tasm_delegate.h"
#include "devtool/lynx_devtool/element/element_inspector.h"
namespace lynx {
namespace devtool {
namespace testing {

class InspectorCSSContractTest : public ContractAgentTest {};
TEST_F(InspectorCSSContractTest, ComputesStylesForAttachedNode) {
  auto delegate =
      std::make_shared<::testing::NiceMock<tasm::test::MockTasmDelegate>>();
  tasm::ElementManager manager(std::make_unique<tasm::MockPaintingContext>(),
                               delegate.get(),
                               tasm::LynxEnvConfig(1080, 1920, 1.f, 1.f));
  auto element = manager.CreateFiberElement("view");
  element->MarkCanBeLayoutOnly(false);
  ElementInspector::InitForInspector(std::make_tuple(element.get()));
  element->CreateElementContainer(false);
  element->MarkAttached();
  tasm->element_root_ = element.get();
  InspectorCSSAgentNG agent(mediator);
  auto request = Request("CSS.getComputedStyleForNode");
  request["params"]["nodeId"] = ElementInspector::NodeId(element.get());
  auto response = Dispatch(agent, request);
  EXPECT_TRUE(response["result"]["computedStyle"].isArray());
  tasm->element_root_ = nullptr;
}
TEST_F(InspectorCSSContractTest, TracksRuleUsageAndAcknowledgesUpdates) {
  InspectorCSSAgentNG agent(mediator);
  for (const char* name : {"CSS.enable", "CSS.startRuleUsageTracking",
                           "CSS.updateRuleUsageTracking",
                           "CSS.stopRuleUsageTracking", "CSS.disable"}) {
    auto request = Request(name);
    if (std::string(name) == "CSS.updateRuleUsageTracking")
      request["params"]["selector"].append("view");
    EXPECT_TRUE(Dispatch(agent, request)["result"].isObject()) << name;
  }
}
TEST_F(InspectorCSSContractTest, RejectsInvalidNestedEditBeforeChangingStyles) {
  InspectorCSSAgentNG agent(mediator);
  auto request = Request("CSS.setStyleTexts");
  auto& edit = request["params"]["edits"][0];
  edit["styleSheetId"] = "1";
  edit["text"] = "color:red";
  for (const char* key : {"startLine", "endLine", "startColumn", "endColumn"})
    edit["range"][key] = 0;
  edit["range"]["startLine"] = Json::Value(Json::objectValue);
  harness->Expect(ContractViolation::Kind::kInvalidParams, "CSS.setStyleTexts");
  EXPECT_EQ(Dispatch(agent, request)["error"]["code"], -32602);
}
}  // namespace testing
}  // namespace devtool
}  // namespace lynx

// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.
// clang-format off
#include "devtool/testing/agent/contract_agent_test.h"
// clang-format on
#include "devtool/lynx_devtool/agent/domain_agent/inspector_ui_tree_agent.h"
namespace lynx {
namespace devtool {
namespace testing {

class UITreeContractFacade : public lynx::testing::DevToolPlatformFacadeMock {
 public:
  std::string GetLynxUITree() override {
    return R"({"name":"view","id":1,"frame":[0,0,100,50],"children":[]})";
  }
};
class InspectorUITreeContractTest : public ContractAgentTest {};
TEST_F(InspectorUITreeContractTest, DisabledTreeMethodsReturnErrors) {
  InspectorUITreeAgent agent(mediator);
  for (const char* name : {"UITree.getLynxUITree", "UITree.getUIInfoForNode",
                           "UITree.setUIStyle"}) {
    auto request = Request(name);
    if (std::string(name) != "UITree.getLynxUITree")
      request["params"]["UINodeId"] = 1;
    if (std::string(name) == "UITree.setUIStyle") {
      request["params"]["styleName"] = "visible";
      request["params"]["styleContent"] = "true";
    }
    EXPECT_EQ(Dispatch(agent, request)["error"]["code"], -32000);
  }
}
TEST_F(InspectorUITreeContractTest, ChecksTreeBothWithAndWithoutCompression) {
  InspectorUITreeAgent agent(mediator);
  auto platform = std::make_shared<UITreeContractFacade>();
  ui_thread->GetTaskRunner()->PostSyncTask(
      [&] { ui->SetDevToolPlatformFacade(platform); });
  for (bool compressed : {false, true}) {
    auto request = Request("UITree.enable");
    request["params"]["useCompression"] = compressed;
    request["params"]["compressionThreshold"] = 1;
    EXPECT_TRUE(Dispatch(agent, request)["result"].isObject());
    auto response = Dispatch(agent, Request("UITree.getLynxUITree"));
    EXPECT_EQ(response["result"]["compress"], compressed);
    EXPECT_EQ(response["result"]["root"].isString(), compressed);
  }
  auto request = Request("UITree.getUIInfoForNode");
  request["params"]["UINodeId"] = 1;
  EXPECT_TRUE(Dispatch(agent, request)["result"].isObject());
  request = Request("UITree.setUIStyle");
  request["params"]["UINodeId"] = 1;
  request["params"]["styleName"] = "visible";
  request["params"]["styleContent"] = "true";
  EXPECT_TRUE(Dispatch(agent, request)["result"].isObject());
  EXPECT_TRUE(Dispatch(agent, Request("UITree.disable"))["result"].isObject());
}
TEST_F(InspectorUITreeContractTest, MissingCompressionRunnerReturnsError) {
  InspectorUITreeAgent agent(mediator);
  EXPECT_TRUE(Dispatch(agent, Request("UITree.enable"))["result"].isObject());
  mediator->default_task_runner_ = nullptr;
  EXPECT_EQ(Dispatch(agent, Request("UITree.getLynxUITree"))["error"]["code"],
            -32603);
}
}  // namespace testing
}  // namespace devtool
}  // namespace lynx

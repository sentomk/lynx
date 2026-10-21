// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.
// clang-format off
#include "devtool/testing/agent/contract_agent_test.h"
// clang-format on
#include "devtool/lynx_devtool/agent/domain_agent/inspector_animation_agent.h"
namespace lynx {
namespace devtool {
namespace testing {

class InspectorAnimationContractTest : public ContractAgentTest {};
TEST_F(InspectorAnimationContractTest, ChecksControlsAndMissingAnimationError) {
  InspectorAnimationAgent agent(mediator);
  for (const char* name :
       {"Animation.enable", "Animation.seekAnimations", "Animation.setPaused",
        "Animation.releaseAnimations", "Animation.disable"}) {
    auto request = Request(name);
    if (std::string(name) == "Animation.seekAnimations" ||
        std::string(name) == "Animation.setPaused" ||
        std::string(name) == "Animation.releaseAnimations")
      request["params"]["animations"] = Json::Value(Json::arrayValue);
    if (std::string(name) == "Animation.seekAnimations")
      request["params"]["currentTime"] = 0;
    if (std::string(name) == "Animation.setPaused")
      request["params"]["paused"] = true;
    EXPECT_TRUE(Dispatch(agent, request)["result"].isObject()) << name;
  }
  auto request = Request("Animation.getCurrentTime");
  request["params"]["id"] = "99999";
  EXPECT_EQ(Dispatch(agent, request)["error"]["code"], -32602);
}
TEST_F(InspectorAnimationContractTest, RejectsInvalidAnimationArrayMember) {
  InspectorAnimationAgent agent(mediator);
  auto request = Request("Animation.setPaused");
  request["params"]["animations"].append(1);
  request["params"]["paused"] = true;
  harness->Expect(ContractViolation::Kind::kInvalidParams,
                  "Animation.setPaused");
  EXPECT_EQ(Dispatch(agent, request)["error"]["code"], -32602);
}
}  // namespace testing
}  // namespace devtool
}  // namespace lynx

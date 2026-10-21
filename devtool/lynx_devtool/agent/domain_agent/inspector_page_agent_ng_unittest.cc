// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.
// clang-format off
#include "devtool/testing/agent/contract_agent_test.h"
// clang-format on
#include "devtool/lynx_devtool/agent/domain_agent/inspector_page_agent_ng.h"
namespace lynx {
namespace devtool {
namespace testing {

class InspectorPageContractTest : public ContractAgentTest {};
TEST_F(InspectorPageContractTest, ChecksResourceTreeAndWelcomeEvent) {
  InspectorPageAgentNG agent(mediator);
  for (const char* name :
       {"Page.enable", "Page.canEmulate", "Page.canScreencast",
        "Page.getResourceTree", "Page.reload"}) {
    EXPECT_TRUE(Dispatch(agent, Request(name))["result"].isObject()) << name;
  }
}
TEST_F(InspectorPageContractTest, StartsAcknowledgesAndStopsScreencast) {
  InspectorPageAgentNG agent(mediator);
  auto request = Request("Page.startScreencast");
  request["params"]["format"] = "jpeg";
  request["params"]["quality"] = 80;
  EXPECT_TRUE(Dispatch(agent, request)["result"].isObject());
  EXPECT_EQ(facade->screen_cast_requests_.size(), 1u);
  request = Request("Page.screencastFrameAck");
  request["params"]["sessionId"] = 1;
  EXPECT_TRUE(Dispatch(agent, request)["result"].isObject());
  EXPECT_TRUE(
      Dispatch(agent, Request("Page.stopScreencast"))["result"].isObject());
  ui_thread->GetTaskRunner()->PostSyncTask(
      [&] { ui->SendPageScreencastVisibilityChangedEvent(true); });
}
TEST_F(InspectorPageContractTest, RecordsLegacyNavigationFrameShape) {
  InspectorPageAgentNG agent(mediator);
  auto request = Request("Page.navigate");
  request["params"]["url"] = "about:blank";
  // Existing Lynx navigation omits result.frameId and emits a minimal frame.
  // Keep these visible until navigation defines a complete frame identity.
  harness->Expect(ContractViolation::Kind::kInvalidResult, "Page.navigate");
  harness->Expect(ContractViolation::Kind::kInvalidEvent,
                  "Page.frameNavigated");
  EXPECT_TRUE(Dispatch(agent, request)["result"].isObject());
}
}  // namespace testing
}  // namespace devtool
}  // namespace lynx

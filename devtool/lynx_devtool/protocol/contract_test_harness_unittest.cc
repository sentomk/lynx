// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.
#include "devtool/lynx_devtool/protocol/contract_test_harness.h"

#include <cstdlib>

#include "devtool/base_devtool/native/public/cdp_responder.h"
#include "third_party/googletest/googletest/include/gtest/gtest-spi.h"
#include "third_party/googletest/googletest/include/gtest/gtest.h"
namespace lynx {
namespace devtool {
namespace {
class Sender : public MessageSender {
 public:
  void SendMessage(const std::string&, const Json::Value&) override {}
  void SendMessage(const std::string&, const std::string&) override {}
};
class Agent : public CDPDomainAgentBase {
 public:
  enum Mode { Valid, Typo, Abandon, Transfer };
  explicit Agent(Mode mode) : mode_(mode) {}
  void CallMethod(const std::shared_ptr<CDPResponder>& responder,
                  const Json::Value&) override {
    if (mode_ == Abandon) return;
    if (mode_ == Transfer) {
      responder->RetrieveSender();
      return;
    }
    Json::Value result(Json::objectValue);
    result[mode_ == Typo ? "runtimeLevell" : "runtimeLevel"] = "INFO";
    result["nativeCompileLevel"] = "MONITOR";
    responder->SendSuccess(result);
  }

 private:
  Mode mode_;
};
void ExerciseAgent(Agent::Mode mode, bool expect = false) {
  ContractTestHarness harness;
  if (expect)
    harness.Expect(ContractViolation::Kind::kInvalidResult, "Lynx.getLogLevel");
  auto sender = harness.Wrap(std::make_shared<Sender>());
  Agent agent(mode);
  Json::Value request(Json::objectValue);
  request["method"] = "Lynx.getLogLevel";
  request["id"] = 1;
  harness.Dispatch(agent, sender, request);
}
TEST(ContractTestHarnessTest, ValidAndExpectedViolations) {
  ExerciseAgent(Agent::Valid);
  ExerciseAgent(Agent::Typo, true);
}
TEST(ContractTestHarnessTest, InvalidResultFails) {
  EXPECT_NONFATAL_FAILURE(ExerciseAgent(Agent::Typo), "result.runtimeLevell");
}
TEST(ContractTestHarnessTest, UnansweredFails) {
  EXPECT_NONFATAL_FAILURE(ExerciseAgent(Agent::Transfer), "kUnanswered");
}
TEST(ContractTestHarnessTest, DroppedFailsOnce) {
  EXPECT_NONFATAL_FAILURE(ExerciseAgent(Agent::Abandon), "kDropped");
}
TEST(ContractTestHarnessTest, MissingExpectationFails) {
  EXPECT_NONFATAL_FAILURE(ExerciseAgent(Agent::Valid, true),
                          "Expected violation did not occur");
}
TEST(ContractTestHarnessTest, RestoresPreviousObserver) {
  auto previous = ContractChecker::Create({});
  SetCDPTrafficObserver(previous);
  ExerciseAgent(Agent::Valid);
  EXPECT_EQ(ExchangeCDPTrafficObserver(nullptr), previous);
}
TEST(ContractTestHarnessTest, ExpectedViolationIsPrintedImmediately) {
  ::testing::internal::CaptureStderr();
  ExerciseAgent(Agent::Typo, true);
  auto output = ::testing::internal::GetCapturedStderr();
  EXPECT_NE(output.find("result.runtimeLevell: unexpected field"),
            std::string::npos);
  EXPECT_NE(output.find("(expected)"), std::string::npos);
}
TEST(ContractTestHarnessDeathTest, PrintsBeforeHandlerAborts) {
  EXPECT_DEATH(
      {
        ContractTestHarness harness;
        auto sender = harness.Wrap(std::make_shared<Sender>());
        class AbortingAgent : public CDPDomainAgentBase {
          void CallMethod(const std::shared_ptr<CDPResponder>&,
                          const Json::Value&) override {
            std::abort();
          }
        } agent;
        Json::Value request(Json::objectValue);
        request["method"] = "Input.emulateTouchFromMouseEvent";
        request["id"] = 42;
        request["params"]["type"] = "mousePressed";
        request["params"]["button"] = "left";
        request["params"]["x"] = "120";
        request["params"]["y"] = 240;
        harness.Dispatch(agent, sender, request);
      },
      "params.x: expected integer");
}
}  // namespace
}  // namespace devtool
}  // namespace lynx

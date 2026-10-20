// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.
#include "devtool/lynx_devtool/protocol/contract_test_harness.h"

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
}  // namespace
}  // namespace devtool
}  // namespace lynx

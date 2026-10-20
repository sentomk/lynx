// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.
#include "devtool/lynx_devtool/protocol/contract_test_harness.h"

#include <algorithm>
#include <mutex>
#include <utility>
#include <vector>

#include "devtool/base_devtool/native/public/cdp_responder.h"
#include "third_party/googletest/googletest/include/gtest/gtest.h"
namespace lynx {
namespace devtool {
namespace {
class CheckedSender : public MessageSender {
 public:
  CheckedSender(std::shared_ptr<MessageSender> sender,
                std::shared_ptr<ContractChecker> checker)
      : sender_(std::move(sender)), checker_(std::move(checker)) {}
  void SendMessage(const std::string& type,
                   const Json::Value& message) override {
    if (type == "CDP") checker_->OnOutgoingMessage(this, message);
    sender_->SendMessage(type, message);
  }
  void SendMessage(const std::string& type,
                   const std::string& message) override {
    if (type == "CDP") {
      Json::Value value;
      Json::Reader reader;
      if (reader.parse(message, value, false))
        checker_->OnOutgoingMessage(this, value);
      else
        ADD_FAILURE() << "Invalid CDP JSON in test sender";
    }
    sender_->SendMessage(type, message);
  }

 private:
  std::shared_ptr<MessageSender> sender_;
  std::shared_ptr<ContractChecker> checker_;
};
}  // namespace
struct ContractTestHarness::State {
  std::mutex mutex;
  std::vector<ContractViolation> violations;
  std::vector<std::pair<ContractViolation::Kind, std::string>> expected;
};
ContractTestHarness::ContractTestHarness() : state_(std::make_shared<State>()) {
  checker_ = ContractChecker::Create([state = state_](const auto& violation) {
    std::lock_guard<std::mutex> lock(state->mutex);
    state->violations.push_back(violation);
  });
  if (!checker_) {
    ADD_FAILURE() << "Cannot load embedded CDP contract";
    return;
  }
  SetCDPTrafficObserver(checker_);
}
ContractTestHarness::~ContractTestHarness() {
  if (!checker_) return;
  checker_->Flush();
  SetCDPTrafficObserver(nullptr);
  std::lock_guard<std::mutex> lock(state_->mutex);
  for (const auto& v : state_->violations) {
    auto expected = std::find(state_->expected.begin(), state_->expected.end(),
                              std::make_pair(v.kind, v.method));
    if (expected != state_->expected.end()) {
      state_->expected.erase(expected);
      continue;
    }
    ADD_FAILURE() << ContractViolationKindName(v.kind) << " " << v.method
                  << " id=" << v.id << " "
                  << ::testing::PrintToString(v.problems);
  }
  for (const auto& expected : state_->expected) {
    ADD_FAILURE() << "Expected violation did not occur: "
                  << ContractViolationKindName(expected.first) << " "
                  << expected.second;
  }
}
std::shared_ptr<MessageSender> ContractTestHarness::Wrap(
    std::shared_ptr<MessageSender> sender) {
  if (!checker_ || !sender) {
    ADD_FAILURE() << "Wrap requires a checker and sender";
    return sender;
  }
  auto existing = wrappers_.find(sender.get());
  if (existing != wrappers_.end() && !existing->second.expired()) return sender;
  auto wrapper = std::make_shared<CheckedSender>(std::move(sender), checker_);
  wrappers_[wrapper.get()] = wrapper;
  return wrapper;
}
void ContractTestHarness::Dispatch(CDPDomainAgentBase& agent,
                                   const std::shared_ptr<MessageSender>& sender,
                                   const Json::Value& request) {
  auto wrapper = wrappers_.find(sender.get());
  if (!checker_ || wrapper == wrappers_.end() || wrapper->second.expired()) {
    ADD_FAILURE() << "Dispatch requires the sender returned by Wrap";
    return;
  }
  if (!request.isObject() || !request["id"].isInt64() ||
      !request["method"].isString()) {
    ADD_FAILURE()
        << "Dispatch requires an object with a method and signed integer id";
    return;
  }
  checker_->OnIncomingMessage(sender.get(), request);
  auto responder =
      std::make_shared<CDPResponder>(sender, request["id"].asInt64());
  agent.CallMethod(responder, request);
}
void ContractTestHarness::Expect(ContractViolation::Kind kind,
                                 const std::string& method) {
  std::lock_guard<std::mutex> lock(state_->mutex);
  state_->expected.emplace_back(kind, method);
}
}  // namespace devtool
}  // namespace lynx

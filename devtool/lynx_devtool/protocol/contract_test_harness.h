// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.
#ifndef DEVTOOL_LYNX_DEVTOOL_PROTOCOL_CONTRACT_TEST_HARNESS_H_
#define DEVTOOL_LYNX_DEVTOOL_PROTOCOL_CONTRACT_TEST_HARNESS_H_
#include <map>
#include <memory>
#include <string>

#include "devtool/base_devtool/native/public/cdp_domain_agent_base.h"
#include "devtool/lynx_devtool/protocol/contract_checker.h"
namespace lynx {
namespace devtool {
// Own one harness per test and drain every worker before destroying it.
class ContractTestHarness {
 public:
  ContractTestHarness();
  ~ContractTestHarness();
  ContractTestHarness(const ContractTestHarness&) = delete;
  ContractTestHarness& operator=(const ContractTestHarness&) = delete;
  std::shared_ptr<MessageSender> Wrap(std::shared_ptr<MessageSender> sender);
  void Dispatch(CDPDomainAgentBase& agent,
                const std::shared_ptr<MessageSender>& sender,
                const Json::Value& request);
  // Each call consumes exactly one matching violation. Missing expectations
  // fail.
  void Expect(ContractViolation::Kind kind, const std::string& method);

 private:
  struct State;
  std::shared_ptr<State> state_;
  std::shared_ptr<ContractChecker> checker_;
  std::map<const void*, std::weak_ptr<MessageSender>> wrappers_;
};
}  // namespace devtool
}  // namespace lynx
#endif  // DEVTOOL_LYNX_DEVTOOL_PROTOCOL_CONTRACT_TEST_HARNESS_H_

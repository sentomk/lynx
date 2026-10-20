// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.
#ifndef DEVTOOL_LYNX_DEVTOOL_PROTOCOL_CONTRACT_CHECKER_H_
#define DEVTOOL_LYNX_DEVTOOL_PROTOCOL_CONTRACT_CHECKER_H_

#include <cstdint>
#include <functional>
#include <list>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "devtool/base_devtool/native/public/cdp_traffic_observer.h"

namespace lynx {
namespace devtool {

struct ContractViolation {
  enum class Kind {
    kInvalidParams,
    kInvalidResult,
    kInvalidError,
    kInvalidEvent,
    kUnknownMethod,
    kUnknownEvent,
    kDuplicateResponse,
    kDropped,
    kUnanswered,
  };
  Kind kind;
  std::string method;
  int64_t id = 0;
  std::vector<std::string> problems;
};
const char* ContractViolationKindName(ContractViolation::Kind kind);

// Test-only observer. Does not block traffic. Reporters can run concurrently
// and must synchronize their own state; they are never called under mutex_.
class ContractChecker : public CDPTrafficObserver {
 public:
  using Reporter = std::function<void(const ContractViolation&)>;
  static std::shared_ptr<ContractChecker> Create(Reporter reporter);
  // Test fixtures can supply a small contract instead of the repository data.
  static std::shared_ptr<ContractChecker> Create(std::string_view json,
                                                 Reporter reporter);
  ~ContractChecker() override;
  void OnIncomingMessage(const void* channel,
                         const Json::Value& message) override;
  void OnOutgoingMessage(const void* channel,
                         const Json::Value& message) override;
  void Flush();

 private:
  struct Contract;
  using Key = std::pair<uintptr_t, int64_t>;
  struct Pending {
    std::string method;
    std::list<Key>::iterator position;
    bool dropped = false;
  };
  ContractChecker(std::shared_ptr<const Contract> contract, Reporter reporter);
  void Report(ContractViolation::Kind kind, const std::string& method,
              int64_t id, std::vector<std::string> problems);
  std::shared_ptr<const Contract> contract_;
  Reporter reporter_;
  std::mutex mutex_;
  std::map<Key, Pending> pending_;
  std::list<Key> order_;
  std::map<Key, std::string> completed_;
  bool overflow_reported_ = false;
};

}  // namespace devtool
}  // namespace lynx
#endif  // DEVTOOL_LYNX_DEVTOOL_PROTOCOL_CONTRACT_CHECKER_H_

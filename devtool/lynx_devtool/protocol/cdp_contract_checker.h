// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#ifndef DEVTOOL_LYNX_DEVTOOL_PROTOCOL_CDP_CONTRACT_CHECKER_H_
#define DEVTOOL_LYNX_DEVTOOL_PROTOCOL_CDP_CONTRACT_CHECKER_H_

#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <utility>
#include <vector>

#include "devtool/base_devtool/native/public/cdp_traffic_observer.h"
#include "third_party/jsoncpp/include/json/json.h"

namespace lynx {
namespace devtool {

struct CDPContractViolation {
  enum class Kind {
    // Request params do not match the command contract.
    kInvalidRequest,
    // A success result does not match the command contract.
    kInvalidResult,
    // An error response is not { code: integer, message: string }.
    kInvalidError,
    // Event params do not match the event contract.
    kInvalidEvent,
    // An event without any contract.
    kUndeclaredEvent,
    // A response whose id has no outstanding request on its channel, such as
    // a second response to the same request.
    kUnmatchedResponse,
  };

  Kind kind;
  // CDP method of the request or event; empty when unknown.
  std::string method;
  // Human-readable problems, one per mismatching field.
  std::vector<std::string> problems;
};

const char* CDPContractViolationKindName(CDPContractViolation::Kind kind);

/**
 * Checks CDP traffic against the effective CDP contract generated from
 * custom_cdp_docs, cdp_support.yaml and the vendored upstream schema.
 *
 * Requests are matched with responses by channel and id. Outputs
 * (results and event params) are checked strictly, so undeclared fields are
 * reported. Requests tolerate unknown fields, like the dispatcher does.
 *
 * The checker only observes and reports; it never changes or blocks messages.
 */
class CDPContractChecker : public CDPTrafficObserver {
 public:
  using Reporter = std::function<void(const CDPContractViolation&)>;

  // Uses the generated contract from cdp_contract_schema.generated.cc.
  static std::shared_ptr<CDPContractChecker> CreateWithGeneratedContract(
      Reporter reporter);
  // Returns nullptr if `schema_json` is not a valid contract schema.
  static std::shared_ptr<CDPContractChecker> Create(
      const std::string& schema_json, Reporter reporter);

  ~CDPContractChecker() override;

  void OnIncomingMessage(const void* channel,
                         const Json::Value& message) override;
  void OnOutgoingMessage(const void* channel,
                         const Json::Value& message) override;

 private:
  struct Contract;
  using PendingKey = std::pair<const void*, int64_t>;

  CDPContractChecker(std::unique_ptr<Contract> contract, Reporter reporter);

  void CheckResponse(const void* channel, const Json::Value& message);
  void CheckEvent(const Json::Value& message);
  void Report(CDPContractViolation::Kind kind, const std::string& method,
              std::vector<std::string> problems);

  std::unique_ptr<Contract> contract_;
  Reporter reporter_;

  std::mutex mutex_;
  // Outstanding requests, mapped to their method. Bounded so requests that
  // never get a response cannot grow it without limit.
  std::map<PendingKey, std::string> pending_;
};

}  // namespace devtool
}  // namespace lynx

#endif  // DEVTOOL_LYNX_DEVTOOL_PROTOCOL_CDP_CONTRACT_CHECKER_H_

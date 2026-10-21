// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#include <cstdlib>
#include <iostream>
#include <optional>

#include "cdpc/method.h"
#include "cdpc_contracts.generated.h"

const lynx::devtool::cdpc::MethodContract* FromSecondTranslationUnit();

namespace {
using namespace lynx::devtool::cdpc;
using lynx::devtool::CDPErrorCode;

void Require(bool condition, const std::string& message) {
  if (!condition) {
    std::cerr << message << '\n';
    std::exit(EXIT_FAILURE);
  }
}

class InputDomain {
 public:
  void InsertText(Reply reply, Params params) {
    std::string message;
    auto text = params.Get<std::string>("text", message);
    Require(text.has_value(), message);
    last_text = text.value();
    retained_reply = std::move(reply);
  }
  std::string last_text;
  std::optional<Reply> retained_reply;
};
constexpr Method<InputDomain> kMethods[] = {
    {methods::kInputInsertText, &InputDomain::InsertText}};
static_assert(HasUniqueNames(kMethods));
static_assert(methods::kInputInsertText.name == "Input.insertText");
}  // namespace

int main() {
  std::string message;
  for (const auto& contract : methods::kContracts) {
    Require(ValidateContract(contract, message).has_value(), message);
  }
  // Inline static storage must have the same address across translation units.
  Require(FromSecondTranslationUnit() == &methods::kInputInsertText,
          "generated descriptors must have program lifetime and one identity");
  for (const char* omitted :
       {"Lynx.getVersion", "Replay.end", "Template.templateConfigInfo"}) {
    Require(methods::Find(omitted) == nullptr,
            "scalar roots must not become object descriptors");
  }
  Require(methods::Find("Input.unknown") == nullptr,
          "unknown method lookup must not select a fallback");
  const auto& contract = methods::kInputInsertText;
  Json::Value raw(Json::objectValue);
  raw["text"] = 1;
  auto invalid = Params::Parse(raw, contract, message);
  Require(
      !invalid.has_value() && invalid.error() == CDPErrorCode::InvalidParams,
      "generated text parameter rejects the wrong type");
  raw["text"] = "generated and owned";
  raw["futureField"] = true;
  auto valid = Params::Parse(raw, contract, message);
  Require(valid.has_value(), message);
  InputDomain domain;
  int successes = 0, errors = 0;
  auto reply = MakeReply(
      7, contract,
      [&](Json::Value result) {
        Require(result.isObject(), "success result is an object");
        ++successes;
      },
      [&](CDPErrorCode, const std::string&) { ++errors; },
      [](std::string_view) {});
  (domain.*kMethods[0].handler)(reply, std::move(valid.value()));
  raw.clear();
  Require(domain.last_text == "generated and owned" && successes == 0,
          "owned parameter and deferred reply survive input destruction");
  domain.retained_reply->SendSuccess();
  reply->SendError(CDPErrorCode::ServerError, "duplicate");
  Require(successes == 1 && errors == 0,
          "one completion for generated contract");
  Json::Value result(Json::objectValue);
  result["extra"] = true;
  Require(!ValidateResult(result, contract, message).has_value(),
          "generated empty result rejects undeclared fields");
  // The production DOM type graph is recursive. Both levels use Node's schema.
  const auto* describe = methods::Find("DOM.describeNode");
  Require(describe != nullptr, "recursive DOM contract is generated");
  Json::Value node(Json::objectValue);
  node["nodeId"] = 1;
  node["backendNodeId"] = 1;
  node["nodeType"] = 1;
  node["nodeName"] = "view";
  node["localName"] = "view";
  node["nodeValue"] = "";
  result.clear();
  result["node"] = node;
  result["node"]["children"].append(node);
  Require(ValidateResult(result, *describe, message).has_value(), message);
  result["node"]["children"][0]["nodeId"] = "wrong";
  Require(!ValidateResult(result, *describe, message).has_value(),
          "recursive generated references retain child constraints");
  std::cout << methods::kContracts.size()
            << " generated contracts validated; Input handoff and recursive "
               "DOM passed\n";
}

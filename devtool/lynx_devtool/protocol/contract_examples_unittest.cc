// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.
#include "devtool/lynx_devtool/protocol/contract_checker.h"
#include "third_party/googletest/googletest/include/gtest/gtest.h"
namespace lynx {
namespace devtool {
namespace protocol {
std::string_view ContractJson();
}
TEST(ContractExamplesTest, SupportedExamplesMatchTheContract) {
  Json::Value contract;
  Json::Reader reader;
  const auto json = protocol::ContractJson();
  ASSERT_TRUE(
      reader.parse(json.data(), json.data() + json.size(), contract, false));
  ASSERT_FALSE(contract["examples"].empty());
  for (const auto& example : contract["examples"]) {
    SCOPED_TRACE(example["entry"].asString() + ": " +
                 example["title"].asString());
    std::vector<ContractViolation> violations;
    auto checker = ContractChecker::Create(
        [&](const auto& v) { violations.push_back(v); });
    ASSERT_NE(checker, nullptr);
    if (example.isMember("request"))
      checker->OnIncomingMessage(checker.get(), example["request"]);
    if (example.isMember("response"))
      checker->OnOutgoingMessage(checker.get(), example["response"]);
    if (example.isMember("event"))
      checker->OnOutgoingMessage(checker.get(), example["event"]);
    const auto& response = example["response"];
    const bool invalid_request = response.isObject() &&
                                 response["error"].isObject() &&
                                 (response["error"]["code"] == -32602 ||
                                  response["error"]["code"] == -32600);
    for (const auto& v : violations) {
      // Error examples deliberately send invalid input. Output must still
      // match.
      if (invalid_request && v.kind == ContractViolation::Kind::kInvalidParams)
        continue;
      ADD_FAILURE() << ContractViolationKindName(v.kind) << " " << v.method
                    << ": " << ::testing::PrintToString(v.problems);
    }
    // Documentation examples are message fragments, not full sessions. Only a
    // test harness or complete trace owns an unanswered-request Flush boundary.
  }
}
}  // namespace devtool
}  // namespace lynx

// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.
#include "devtool/lynx_devtool/protocol/contract_checker.h"

#include <thread>

#include "third_party/googletest/googletest/include/gtest/gtest.h"

namespace lynx {
namespace devtool {
namespace protocol {
std::string_view ContractCasesJson();
std::string_view CaseContractJson();
}  // namespace protocol
namespace {
using Kind = ContractViolation::Kind;
Json::Value Parse(std::string_view text) {
  Json::Value value;
  Json::Reader reader;
  EXPECT_TRUE(
      reader.parse(text.data(), text.data() + text.size(), value, false));
  return value;
}
Json::Value Request(const std::string& method = "Lynx.getLogLevel",
                    int64_t id = 1) {
  Json::Value request(Json::objectValue);
  request["method"] = method;
  request["id"] = static_cast<Json::Int64>(id);
  return request;
}
Json::Value Response(int64_t id = 1) {
  auto response = Parse(
      R"({"result":{"runtimeLevel":"INFO","nativeCompileLevel":"MONITOR"}})");
  response["id"] = static_cast<Json::Int64>(id);
  return response;
}
class ContractCheckerTest : public ::testing::Test {
 protected:
  void SetUp() override {
    checker = ContractChecker::Create(
        protocol::CaseContractJson(),
        [this](const auto& v) { violations.push_back(v); });
    ASSERT_NE(checker, nullptr);
  }
  std::vector<ContractViolation> violations;
  std::shared_ptr<ContractChecker> checker;
};
TEST_F(ContractCheckerTest, SharedPythonCases) {
  for (const auto& test : Parse(protocol::ContractCasesJson())) {
    SCOPED_TRACE(test["name"].asString());
    violations.clear();
    const auto method = test["schema_entry"].asString();
    const auto direction = test["direction"].asString();
    if (direction == "event") {
      Json::Value message(Json::objectValue);
      message["method"] = method;
      message["params"] = test["value"];
      checker->OnOutgoingMessage(this, message);
    } else {
      auto request = Request(method);
      if (direction == "params") request["params"] = test["value"];
      checker->OnIncomingMessage(this, request);
      if (direction == "result") {
        Json::Value response(Json::objectValue);
        response["id"] = 1;
        response["result"] = test["value"];
        checker->OnOutgoingMessage(this, response);
      } else
        checker->OnOutgoingMessage(this, Response());
    }
    std::vector<std::string> actual, expected;
    for (const auto& violation : violations)
      actual.insert(actual.end(), violation.problems.begin(),
                    violation.problems.end());
    for (const auto& problem : test["problems"])
      expected.push_back(problem.asString());
    EXPECT_EQ(actual, expected);
  }
}
TEST_F(ContractCheckerTest, ValidResultAndFlush) {
  checker->OnIncomingMessage(this, Request());
  checker->OnOutgoingMessage(this, Response());
  checker->Flush();
  EXPECT_TRUE(violations.empty());
}
TEST_F(ContractCheckerTest, InvalidRequestAndResult) {
  auto request = Request();
  request["params"] = 3;
  checker->OnIncomingMessage(this, request);
  auto response = Response();
  response["result"].removeMember("runtimeLevel");
  checker->OnOutgoingMessage(this, response);
  ASSERT_EQ(violations.size(), 2u);
  EXPECT_EQ(violations[0].kind, Kind::kInvalidParams);
  EXPECT_EQ(violations[1].kind, Kind::kInvalidResult);
}
TEST_F(ContractCheckerTest, ErrorsAndEnvelope) {
  checker->OnIncomingMessage(this, Request());
  checker->OnOutgoingMessage(
      this,
      Parse(
          R"({"id":1,"error":{"code":-32602,"message":"bad params","data":{}}})"));
  EXPECT_TRUE(violations.empty());
  checker->OnIncomingMessage(this, Request());
  checker->OnOutgoingMessage(
      this, Parse(R"({"id":1,"error":{"code":"wrong","message":42}})"));
  ASSERT_EQ(violations.size(), 1u);
  EXPECT_EQ(violations[0].kind, Kind::kInvalidError);
  checker->OnIncomingMessage(this, Request());
  checker->OnOutgoingMessage(this, Parse(R"({"id":1,"result":{},"error":{}})"));
  EXPECT_EQ(violations.back().kind, Kind::kInvalidResult);
}
TEST_F(ContractCheckerTest, EventsAndUnknownMethods) {
  checker->OnOutgoingMessage(
      this,
      Parse(
          R"({"method":"GlobalProps.changed","params":{"timestamp":1,"changes":[{"operation":"replace"}]}})"));
  EXPECT_TRUE(violations.empty());
  checker->OnOutgoingMessage(
      this, Parse(R"({"method":"GlobalProps.changed","params":{}})"));
  checker->OnOutgoingMessage(this, Parse(R"({"method":"Missing.event"})"));
  checker->OnIncomingMessage(this, Request("Missing.method"));
  ASSERT_EQ(violations.size(), 3u);
  EXPECT_EQ(violations[0].kind, Kind::kInvalidEvent);
  EXPECT_EQ(violations[1].kind, Kind::kUnknownEvent);
  EXPECT_EQ(violations[2].kind, Kind::kUnknownMethod);
}
TEST_F(ContractCheckerTest, DuplicateAndChannelIsolation) {
  int second_channel;
  checker->OnIncomingMessage(this, Request());
  checker->OnIncomingMessage(&second_channel, Request());
  checker->OnOutgoingMessage(this, Response());
  checker->OnOutgoingMessage(&second_channel, Response());
  EXPECT_TRUE(violations.empty());
  checker->OnOutgoingMessage(this, Response());
  ASSERT_EQ(violations.size(), 1u);
  EXPECT_EQ(violations[0].kind, Kind::kDuplicateResponse);
  EXPECT_EQ(violations[0].method, "Lynx.getLogLevel");
  EXPECT_EQ(violations[0].id, 1);
}
TEST_F(ContractCheckerTest, DroppedResponseIsReportedOnlyOnce) {
  checker->OnIncomingMessage(this, Request());
  checker->OnResponseDropped(this, 1);
  checker->OnResponseDropped(this, 1);
  checker->OnOutgoingMessage(this, Parse(R"({"id":1,"result":{}})"));
  checker->Flush();
  ASSERT_EQ(violations.size(), 1u);
  EXPECT_EQ(violations[0].kind, Kind::kDropped);
  EXPECT_EQ(violations[0].method, "Lynx.getLogLevel");
  EXPECT_EQ(violations[0].id, 1);
}

TEST_F(ContractCheckerTest, FlushReportsUnansweredOnce) {
  checker->OnIncomingMessage(this, Request());
  checker->Flush();
  checker->Flush();
  ASSERT_EQ(violations.size(), 1u);
  EXPECT_EQ(violations[0].kind, Kind::kUnanswered);
}
TEST_F(ContractCheckerTest, PendingLimitEvictsOldestAndReportsOnce) {
  for (int id = 5000; id > 902; --id)
    checker->OnIncomingMessage(this, Request("Lynx.getLogLevel", id));
  ASSERT_EQ(violations.size(), 1u);
  EXPECT_EQ(violations[0].id, 5000);  // Arrival order, not sorted id order.
  checker->Flush();
  EXPECT_EQ(violations.size(), 4097u);
}
TEST(ContractSchemaTest, InvalidSchemaAndReferencesAreRejected) {
  EXPECT_EQ(ContractChecker::Create("{}", {}), nullptr);
  auto schema = Parse(protocol::CaseContractJson());
  schema["methods"]["Lynx.getLogLevel"]["returns"]["properties"]["runtimeLevel"]
        ["schema"]["type"] = "string|null";
  EXPECT_EQ(ContractChecker::Create(schema.toStyledString(), {}), nullptr);
  schema = Parse(protocol::CaseContractJson());
  schema["types"]["Cycle"]["ref"] = "Cycle";
  EXPECT_EQ(ContractChecker::Create(schema.toStyledString(), {}), nullptr);
}
TEST(ContractSchemaTest, RecursiveReferencesAndOptionalFields) {
  auto schema = Parse(
      R"({"version":1,"types":{"Node":{"type":"object","properties":{"label":{"schema":{"type":"string"}},"next":{"schema":{"ref":"Node"},"optional":true}}}},"methods":{"Node.get":{"params":{"type":"object","properties":{}},"returns":{"type":"object","properties":{"root":{"schema":{"ref":"Node"}}}}}},"events":{}})");
  std::vector<ContractViolation> violations;
  auto checker = ContractChecker::Create(
      schema.toStyledString(), [&](const auto& v) { violations.push_back(v); });
  ASSERT_NE(checker, nullptr);
  checker->OnIncomingMessage(nullptr, Request("Node.get"));
  checker->OnOutgoingMessage(
      nullptr,
      Parse(
          R"({"id":1,"result":{"root":{"label":"one","next":{"label":"two"}}}})"));
  EXPECT_TRUE(violations.empty());
}
TEST(ContractSchemaTest, ConcurrentChannels) {
  std::mutex mutex;
  std::vector<ContractViolation> violations;
  auto checker =
      ContractChecker::Create(protocol::CaseContractJson(), [&](const auto& v) {
        std::lock_guard<std::mutex> lock(mutex);
        violations.push_back(v);
      });
  ASSERT_NE(checker, nullptr);
  auto work = [&](const void* channel) {
    for (int i = 0; i < 100; ++i) {
      checker->OnIncomingMessage(channel, Request("Lynx.getLogLevel", i));
      checker->OnOutgoingMessage(channel, Response(i));
    }
  };
  int a, b;
  std::thread first(work, &a), second(work, &b);
  first.join();
  second.join();
  checker->Flush();
  EXPECT_TRUE(violations.empty());
}
}  // namespace
}  // namespace devtool
}  // namespace lynx

// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#include <map>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "devtool/base_devtool/native/public/cdp_codec.h"
#include "devtool/base_devtool/native/public/cdp_responder.h"
#include "devtool/base_devtool/native/public/message_sender.h"
#include "devtool/lynx_devtool/protocol/cdp_contract_checker.h"
#include "devtool/lynx_devtool/protocol/cdp_protocol.generated.h"
#include "third_party/googletest/googletest/include/gtest/gtest.h"

namespace lynx {
namespace devtool {
namespace testing {

namespace {

namespace white_board = protocol::white_board;

Json::Value Parse(const std::string& text) {
  Json::Value value;
  Json::Reader reader;
  EXPECT_TRUE(reader.parse(text, value, false)) << text;
  return value;
}

class RecordingSender : public MessageSender {
 public:
  void SendMessage(const std::string& type, const Json::Value& msg) override {
    messages.push_back(msg);
  }
  void SendMessage(const std::string& type, const std::string& msg) override {
    messages.push_back(Parse(msg));
  }
  std::vector<Json::Value> messages;
};

// Records calls and answers synchronously.
class FakeBackend : public white_board::Backend {
 public:
  void Clear(white_board::ClearResponder responder) override {
    calls.push_back("clear");
    responder.SendSuccess();
  }
  void Disable(white_board::DisableResponder responder) override {
    calls.push_back("disable");
    responder.SendSuccess();
  }
  void Enable(white_board::EnableResponder responder) override {
    calls.push_back("enable");
    responder.SendError(CDPErrorCode::ServerError, "not now");
  }
  void GetSharedData(white_board::GetSharedDataResponder responder) override {
    calls.push_back("getSharedData");
    white_board::GetSharedDataResult result;
    result.entries.push_back({"k", "1"});
    responder.SendSuccess(result);
  }
  void RemoveSharedData(white_board::RemoveSharedDataResponder responder,
                        white_board::RemoveSharedDataParams params) override {
    calls.push_back("removeSharedData:" + params.key);
    responder.SendSuccess();
  }
  void SetSharedData(white_board::SetSharedDataResponder responder,
                     white_board::SetSharedDataParams params) override {
    calls.push_back("setSharedData:" + params.key + "=" + params.value);
    responder.SendSuccess();
  }

  std::vector<std::string> calls;
};

class GuardedAgent : public white_board::Agent {
 public:
  using white_board::Agent::Agent;

 protected:
  bool PreDispatch(const std::string& method, const Json::Value& message,
                   const std::shared_ptr<CDPResponder>& responder) override {
    if (message.isMember("sessionId")) {
      responder->SendError(CDPErrorCode::MethodNotFound, "session " + method);
      return false;
    }
    return true;
  }
};

}  // namespace

class CDPGeneratedAgentTest : public ::testing::Test {
 protected:
  void SetUp() override {
    auto backend = std::make_unique<FakeBackend>();
    backend_ = backend.get();
    agent_ = white_board::CreateAgent(std::move(backend));
    ASSERT_NE(agent_, nullptr);
  }

  Json::Value Call(CDPDomainAgentBase& agent, const std::string& text) {
    sender_->messages.clear();
    Json::Value message = Parse(text);
    {
      auto responder =
          std::make_shared<CDPResponder>(sender_, message["id"].asInt64());
      agent.CallMethod(responder, message);
    }
    EXPECT_EQ(sender_->messages.size(), 1u);
    return sender_->messages.empty() ? Json::Value() : sender_->messages[0];
  }

  std::shared_ptr<RecordingSender> sender_ =
      std::make_shared<RecordingSender>();
  FakeBackend* backend_ = nullptr;
  std::unique_ptr<CDPDomainAgentBase> agent_;
};

TEST_F(CDPGeneratedAgentTest, RoutesDecodedCommandsToBackend) {
  EXPECT_EQ(Call(*agent_, R"({"id":1,"method":"WhiteBoard.setSharedData",
                             "params":{"key":"a","value":"1","x":0}})"),
            Parse(R"({"id":1,"result":{}})"));
  EXPECT_EQ(
      Call(*agent_, R"({"id":2,"method":"WhiteBoard.getSharedData"})"),
      Parse(R"({"id":2,"result":{"entries":[{"key":"k","value":"1"}]}})"));
  EXPECT_EQ(Call(*agent_, R"({"id":3,"method":"WhiteBoard.enable"})")["error"],
            Parse(R"({"code":-32000,"message":"not now"})"));
  EXPECT_EQ(backend_->calls,
            (std::vector<std::string>{"setSharedData:a=1", "getSharedData",
                                      "enable"}));
}

TEST_F(CDPGeneratedAgentTest, RejectsInvalidParamsAndUnknownMethods) {
  EXPECT_EQ(
      Call(*agent_, R"({"id":1,"method":"WhiteBoard.removeSharedData",
                             "params":{"key":1}})")["error"],
      Parse(R"({"code":-32602,"message":"params.key: expected string"})"));
  EXPECT_EQ(Call(*agent_, R"({"id":2,"method":"WhiteBoard.nope"})")["error"],
            Parse(R"({"code":-32601,
                      "message":"'WhiteBoard.nope' wasn't found"})"));
  EXPECT_TRUE(backend_->calls.empty());
}

TEST_F(CDPGeneratedAgentTest, PreDispatchRunsBeforeDecoding) {
  auto backend = std::make_unique<FakeBackend>();
  FakeBackend* backend_ptr = backend.get();
  GuardedAgent agent(std::move(backend));
  EXPECT_EQ(Call(agent, R"({"id":1,"method":"WhiteBoard.removeSharedData",
                            "sessionId":"s","params":{"key":1}})")["error"],
            Parse(R"({"code":-32601,
                      "message":"session WhiteBoard.removeSharedData"})"));
  EXPECT_EQ(Call(agent, R"({"id":2,"method":"WhiteBoard.clear"})"),
            Parse(R"({"id":2,"result":{}})"));
  EXPECT_EQ(backend_ptr->calls, (std::vector<std::string>{"clear"}));
}

TEST(CDPGeneratedAgentFactoryTest, RejectsNullBackend) {
  EXPECT_EQ(white_board::CreateAgent(nullptr), nullptr);
}

TEST(CDPCodecTest, DecodesScalarsStrictly) {
  std::string error;
  std::string text;
  int64_t integer = 0;
  double number = 0;
  bool flag = false;
  EXPECT_TRUE(cdp::DecodeValue(Json::Value("a"), "p", &text, &error));
  EXPECT_FALSE(cdp::DecodeValue(Json::Value(1), "p", &text, &error));
  EXPECT_EQ(error, "p: expected string");
  EXPECT_TRUE(cdp::DecodeValue(Json::Value(3), "p", &integer, &error));
  EXPECT_EQ(integer, 3);
  EXPECT_FALSE(cdp::DecodeValue(Json::Value(1.5), "p", &integer, &error));
  EXPECT_TRUE(cdp::DecodeValue(Json::Value(3), "p", &number, &error));
  EXPECT_EQ(number, 3.0);
  EXPECT_FALSE(cdp::DecodeValue(Json::Value("3"), "p", &number, &error));
  EXPECT_TRUE(cdp::DecodeValue(Json::Value(true), "p", &flag, &error));
  EXPECT_FALSE(cdp::DecodeValue(Json::Value(1), "p", &flag, &error));
  EXPECT_EQ(error, "p: expected boolean");
}

TEST(CDPCodecTest, DecodesContainersFieldsAndConstraints) {
  std::string error;
  Json::Value object = Parse(R"({"list":[1,2],"map":{"a":["x"]},"mode":"b"})");

  std::vector<int64_t> list;
  EXPECT_TRUE(cdp::DecodeField(object, "list", "params", &list, &error));
  EXPECT_EQ(list, (std::vector<int64_t>{1, 2}));

  std::map<std::string, std::vector<std::string>> map;
  EXPECT_TRUE(cdp::DecodeField(object, "map", "params", &map, &error));
  EXPECT_EQ(map["a"], (std::vector<std::string>{"x"}));

  std::optional<std::string> missing = "stale";
  EXPECT_TRUE(cdp::DecodeField(object, "absent", "params", &missing, &error));
  EXPECT_FALSE(missing.has_value());

  std::string required;
  EXPECT_FALSE(cdp::DecodeField(object, "absent", "params", &required, &error));
  EXPECT_EQ(error, "params.absent: missing required field");

  std::vector<std::string> wrong;
  EXPECT_FALSE(cdp::DecodeField(object, "list", "params", &wrong, &error));
  EXPECT_EQ(error, "params.list[0]: expected string");

  EXPECT_TRUE(
      cdp::CheckEnum(std::string("b"), "params.mode", {"a", "b"}, &error));
  EXPECT_FALSE(
      cdp::CheckEnum(std::string("c"), "params.mode", {"a", "b"}, &error));
  EXPECT_EQ(error, "params.mode: 'c' is not an allowed value");
  EXPECT_TRUE(cdp::CheckKinds(Json::Value(1), "p", cdp::kNumber, &error));
  EXPECT_FALSE(cdp::CheckKinds(Json::Value("x"), "p", cdp::kObject | cdp::kNull,
                               &error));
}

TEST(CDPCodecTest, EncodesContainersAndOptionalFields) {
  Json::Value object(Json::objectValue);
  cdp::EncodeField(&object, "list", std::vector<int64_t>{1, 2});
  cdp::EncodeField(&object, "map", std::map<std::string, bool>{{"a", true}});
  cdp::EncodeField(&object, "absent", std::optional<std::string>());
  cdp::EncodeField(&object, "present", std::optional<double>(1.5));
  EXPECT_EQ(object, Parse(R"({"list":[1,2],"map":{"a":true},"present":1.5})"));
}

TEST(CDPProtocolTest, DecodesWhiteBoardParams) {
  white_board::SetSharedDataParams params;
  std::string error;
  EXPECT_TRUE(white_board::Decode(
      Parse(R"({"key":"count","value":"3","extra":true})"), &params, &error));
  EXPECT_EQ(params.key, "count");
  EXPECT_EQ(params.value, "3");

  EXPECT_FALSE(white_board::Decode(Parse(R"({"key":"count","value":3})"),
                                   &params, &error));
  EXPECT_EQ(error, "params.value: expected string");

  white_board::RemoveSharedDataParams remove;
  EXPECT_FALSE(white_board::Decode(Json::Value(), &remove, &error));
  EXPECT_EQ(error, "params.key: missing required field");
}

TEST(CDPProtocolTest, EncodedWhiteBoardMessagesMatchContract) {
  std::vector<std::string> problems;
  auto checker = CDPContractChecker::CreateWithGeneratedContract(
      [&problems](const CDPContractViolation& violation) {
        for (const auto& problem : violation.problems) {
          problems.push_back(violation.method + ": " + problem);
        }
      });
  ASSERT_NE(checker, nullptr);
  const void* channel = &problems;

  white_board::GetSharedDataResult result;
  result.entries.push_back({"count", "3"});
  Json::Value encoded = white_board::Encode(result);
  EXPECT_EQ(encoded, Parse(R"({"entries":[{"key":"count","value":"3"}]})"));

  Json::Value response(Json::objectValue);
  response["id"] = 1;
  response["result"] = encoded;
  checker->OnIncomingMessage(
      channel, Parse(R"({"id":1,"method":"WhiteBoard.getSharedData"})"));
  checker->OnOutgoingMessage(channel, response);

  EXPECT_EQ(white_board::Encode(white_board::OnSharedDataUpdated{"k", "2"}),
            Parse(R"({"method":"WhiteBoard.onSharedDataUpdated",
                      "params":{"key":"k","newValue":"2"}})"));
  EXPECT_EQ(white_board::Encode(white_board::OnSharedDataCleared{}),
            Parse(R"({"method":"WhiteBoard.onSharedDataCleared"})"));
  checker->OnOutgoingMessage(
      channel, white_board::Encode(white_board::OnSharedDataAdded{"k", "1"}));
  checker->OnOutgoingMessage(
      channel, white_board::Encode(white_board::OnSharedDataRemoved{"k"}));
  checker->OnOutgoingMessage(
      channel, white_board::Encode(white_board::OnSharedDataCleared{}));
  EXPECT_TRUE(problems.empty()) << ::testing::PrintToString(problems);
}

}  // namespace testing
}  // namespace devtool
}  // namespace lynx

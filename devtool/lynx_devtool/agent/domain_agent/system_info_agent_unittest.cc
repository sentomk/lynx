// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#include "devtool/lynx_devtool/agent/domain_agent/system_info_agent.h"

#include <iostream>
#include <memory>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "cdpc/params.h"
#include "cdpc/reply.h"
#include "core/base/threading/task_runner_manufactor.h"
#include "devtool/base_devtool/native/public/cdp_error_code.h"
#include "devtool/base_devtool/native/public/cdp_responder.h"
#include "devtool/base_devtool/native/public/message_sender.h"
#include "devtool/lynx_devtool/agent/lynx_global_devtool_mediator.h"
#include "third_party/googletest/googletest/include/gtest/gtest.h"

namespace lynx {
namespace devtool {
namespace testing {

// SystemInfoGetInfo assembles and sends the response synchronously, so the
// test sender can capture the last message without any waiting.
class SystemInfoTestMessageSender : public MessageSender {
 public:
  void SendMessage(const std::string& type,
                   const Json::Value& message) override {
    type_ = type;
    message_ = message.toStyledString();
  }

  void SendMessage(const std::string& type,
                   const std::string& message) override {
    type_ = type;
    message_ = message;
  }

  Json::Value Response() const {
    Json::Value response;
    Json::Reader reader;
    EXPECT_EQ(type_, "CDP");
    EXPECT_TRUE(reader.parse(message_, response, false));
    return response;
  }

 private:
  std::string type_;
  std::string message_;
};

class SystemInfoAgentTest : public ::testing::Test {
 protected:
  // LynxGlobalDevToolMediator waits for the UI thread on construction.
  static void SetUpTestSuite() { base::UIThread::Init(); }

  void SetUp() override {
    sender_ = std::make_shared<SystemInfoTestMessageSender>();
    agent_ = std::make_shared<SystemInfoAgent>();
  }

  Json::Value Dispatch(const std::string& method,
                       const Json::Value* params = nullptr) {
    Json::Value message(Json::ValueType::objectValue);
    message["id"] = 11;
    message["method"] = method;
    if (params) {
      message["params"] = *params;
    }
    auto responder = std::make_shared<CDPResponder>(sender_, 11);
    agent_->CallMethod(responder, message);
    return sender_->Response();
  }

  std::shared_ptr<SystemInfoAgent> agent_;
  std::shared_ptr<SystemInfoTestMessageSender> sender_;
};

TEST_F(SystemInfoAgentTest, GetInfoReturnsResult) {
  Json::Value response = Dispatch("SystemInfo.getInfo");

  EXPECT_EQ(response["id"].asInt64(), 11);
  ASSERT_TRUE(response["result"].isObject());
  // GetSystemModelName defaults to "" in the platform facade mock; the platform
  // field is compiled in and always present.
  EXPECT_TRUE(response["result"]["modelName"].isString());
  EXPECT_TRUE(response["result"]["platform"].isString());
  EXPECT_FALSE(response.isMember("error"));
}

TEST_F(SystemInfoAgentTest, ObjectAndNullParamsAreAccepted) {
  const Json::Value empty(Json::objectValue);
  const Json::Value null_params;
  for (const auto* params : {&empty, &null_params}) {
    Json::Value response = Dispatch("SystemInfo.getInfo", params);

    EXPECT_TRUE(response["result"]["modelName"].isString());
    EXPECT_TRUE(response["result"]["platform"].isString());
    EXPECT_FALSE(response.isMember("error"));
  }
}

// Calling the Agent directly skips DevToolMessageDispatcher, which would
// already reject non-object params as InvalidRequest.
TEST_F(SystemInfoAgentTest, ArrayParamsAreRejected) {
  const Json::Value params(Json::arrayValue);
  Json::Value response = Dispatch("SystemInfo.getInfo", &params);

  EXPECT_EQ(response["error"]["code"].asInt(),
            static_cast<int>(CDPErrorCode::InvalidParams));
  EXPECT_FALSE(response.isMember("result"));
}

// Diagnostics name the Lynx code that completed the reply. A contract that
// also requires "extra" makes the real handler's result invalid.
TEST_F(SystemInfoAgentTest, DiagnosticsReportCallerLocations) {
  static constexpr cdpc::Schema kEmpty = cdpc::Schema::Object();
  static constexpr cdpc::Schema kString{cdpc::Type::String};
  static constexpr cdpc::Property kFields[] = {
      {"modelName", &kString}, {"platform", &kString}, {"extra", &kString}};
  static constexpr cdpc::Schema kResult = cdpc::Schema::Object(kFields, 3);
  static constexpr cdpc::MethodContract kStricter{"SystemInfo.getInfo", &kEmpty,
                                                  &kResult};

  std::vector<std::string> diagnostics;
  auto responder = std::make_shared<CDPResponder>(sender_, 11);
  cdpc::Reply reply = cdpc::MakeReply(
      11, kStricter, responder,
      [&](std::string_view message) { diagnostics.emplace_back(message); },
      cdpc::ResultValidation::kReject);
  std::string error;
  auto params =
      cdpc::Params::Parse(Json::Value(Json::objectValue), kStricter, error);
  ASSERT_TRUE(params.has_value()) << error;

  LynxGlobalDevToolMediator::GetInstance().SystemInfoGetInfo(
      reply, std::move(params.value()));
  reply.SendError(CDPErrorCode::ServerError, "late");

  for (const auto& message : diagnostics) {
    std::cout << "[CDPC] " << message << std::endl;
  }
  ASSERT_EQ(diagnostics.size(), 2u);
  EXPECT_NE(diagnostics[0].find("lynx_global_devtool_mediator.cc:"),
            std::string::npos);
  EXPECT_NE(diagnostics[0].find("(SystemInfoGetInfo)"), std::string::npos);
  EXPECT_NE(diagnostics[1].find("system_info_agent_unittest.cc:"),
            std::string::npos);
  EXPECT_NE(diagnostics[1].find("first completed at"), std::string::npos);
  EXPECT_NE(diagnostics[1].find("lynx_global_devtool_mediator.cc:"),
            std::string::npos);

  Json::Value response = sender_->Response();
  EXPECT_EQ(response["error"]["code"].asInt(),
            static_cast<int>(CDPErrorCode::InternalError));
  EXPECT_EQ(response["error"]["message"].asString().find(".cc"),
            std::string::npos);
}

TEST_F(SystemInfoAgentTest, UnknownMethodReturnsMethodNotFound) {
  Json::Value response = Dispatch("SystemInfo.unknown");

  EXPECT_EQ(response["id"].asInt64(), 11);
  EXPECT_EQ(response["error"]["code"].asInt(),
            static_cast<int>(CDPErrorCode::MethodNotFound));
  EXPECT_EQ(response["error"]["message"].asString(),
            "'SystemInfo.unknown' wasn't found");
}

}  // namespace testing
}  // namespace devtool
}  // namespace lynx

// Copyright 2025 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#define protected public
#define private public

#include "devtool/lynx_devtool/shared_data/white_board_inspector_delegate.h"

#include <memory>
#include <string>
#include <vector>

#include "core/runtime/lepus/json_parser.h"
#include "core/shared_data/lynx_white_board.h"
#include "core/value_wrapper/value_impl_lepus.h"
#include "devtool/base_devtool/native/public/cdp_error_code.h"
#include "devtool/base_devtool/native/public/cdp_responder.h"
#include "devtool/base_devtool/native/test/message_sender_mock.h"
#include "devtool/base_devtool/native/test/mock_receiver.h"
#include "devtool/lynx_devtool/protocol/cdp_contract_checker.h"
#include "devtool/testing/mock/white_board_inspector_delegate_mock.h"
#include "third_party/googletest/googletest/include/gtest/gtest.h"

namespace lynx {
namespace devtool {
namespace testing {

namespace white_board = protocol::white_board;

// Calls the delegate directly, standing in for the thread hop that
// InspectorWhiteBoardAgent makes through LynxDevToolMediator.
class DirectBackend : public white_board::Backend {
 public:
  explicit DirectBackend(WhiteBoardInspectorDelegate* delegate)
      : delegate_(delegate) {}

  void Clear(white_board::ClearResponder responder) override {
    delegate_->Clear(responder);
  }
  void Disable(white_board::DisableResponder responder) override {
    delegate_->Disable(responder);
  }
  void Enable(white_board::EnableResponder responder) override {
    delegate_->Enable(responder);
  }
  void GetSharedData(white_board::GetSharedDataResponder responder) override {
    delegate_->GetSharedData(responder);
  }
  void RemoveSharedData(white_board::RemoveSharedDataResponder responder,
                        white_board::RemoveSharedDataParams params) override {
    delegate_->RemoveSharedData(responder, params);
  }
  void SetSharedData(white_board::SetSharedDataResponder responder,
                     white_board::SetSharedDataParams params) override {
    delegate_->SetSharedData(responder, params);
  }

 private:
  WhiteBoardInspectorDelegate* delegate_;
};

class WhiteBoardInspectorDelegateTest : public ::testing::Test {
 public:
  WhiteBoardInspectorDelegateTest() {}
  ~WhiteBoardInspectorDelegateTest() override {}
  void SetUp() override {
    MockReceiver::GetInstance().ResetAll();
    delegate_ = std::make_shared<WhiteBoardInspectorDelegateMock>(1);
    agent_ = white_board::CreateAgent(
        std::make_unique<DirectBackend>(delegate_.get()));
    inspector_ = std::make_shared<WhiteBoardInspectorImpl>();
    white_board_ = std::make_shared<tasm::WhiteBoard>();
    inspector_->SetWhiteBoard(white_board_);
    white_board_->SetInspector(inspector_);
    message_sender_ = std::make_shared<MessageSenderMock>();

    std::string key1 = "key1";
    std::string value1 = "\"value1\"";
    std::string key2 = "key2";
    std::string value2 = "\"value2\"";

    lepus::Value lepus_value1 = lepus::jsonValueTolepusValue(value1.c_str());
    auto data1 = std::make_shared<pub::ValueImplLepus>(lepus_value1);
    white_board_->SetGlobalSharedData(key1, data1);
    lepus::Value lepus_value2 = lepus::jsonValueTolepusValue(value2.c_str());
    auto data2 = std::make_shared<pub::ValueImplLepus>(lepus_value2);
    white_board_->SetGlobalSharedData(key2, data2);
  }

 protected:
  // Dispatches a WhiteBoard request through the generated Agent to the
  // delegate with a fresh CDPResponder and returns the parsed CDP envelope the
  // responder emitted.
  Json::Value RunCommand(const std::string& method, const Json::Value& params,
                         int64_t id = 123) {
    MockReceiver::GetInstance().ResetAll();
    Json::Value message(Json::objectValue);
    message["id"] = static_cast<Json::Int64>(id);
    message["method"] = method;
    if (!params.isNull()) {
      message["params"] = params;
    }
    {
      auto responder = std::make_shared<CDPResponder>(message_sender_, id);
      agent_->CallMethod(responder, message);
    }
    Json::Value response;
    Json::Reader reader;
    reader.parse(MockReceiver::GetInstance().received_message_.second, response,
                 false);
    return response;
  }

  std::shared_ptr<WhiteBoardInspectorDelegateMock> delegate_;
  std::unique_ptr<CDPDomainAgentBase> agent_;
  std::shared_ptr<WhiteBoardInspectorImpl> inspector_;
  std::shared_ptr<tasm::WhiteBoard> white_board_;
  std::shared_ptr<MessageSenderMock> message_sender_;
};

TEST_F(WhiteBoardInspectorDelegateTest, Enable) {
  Json::Value response = RunCommand("WhiteBoard.enable", Json::Value(), 123);
  EXPECT_EQ(response["id"].asInt64(), 123);
  EXPECT_TRUE(response["result"].isObject());
  EXPECT_TRUE(response["result"].empty());
  EXPECT_TRUE(delegate_->enabled_);
}

TEST_F(WhiteBoardInspectorDelegateTest, Disable) {
  delegate_->enabled_ = true;
  Json::Value response = RunCommand("WhiteBoard.disable", Json::Value(), 123);
  EXPECT_EQ(response["id"].asInt64(), 123);
  EXPECT_TRUE(response["result"].isObject());
  EXPECT_FALSE(delegate_->enabled_);
}

TEST_F(WhiteBoardInspectorDelegateTest, SetSharedDataWhenDisabledReturnsError) {
  Json::Value params(Json::ValueType::objectValue);
  params["key"] = "key3";
  params["value"] = "value3";

  Json::Value response = RunCommand("WhiteBoard.setSharedData", params);
  EXPECT_EQ(response["error"]["code"].asInt(),
            static_cast<int>(CDPErrorCode::ServerError));
  EXPECT_EQ(response["error"]["message"].asString(),
            "WhiteBoard is not enabled");
}

TEST_F(WhiteBoardInspectorDelegateTest,
       SetSharedDataWithoutInspectorReturnsError) {
  delegate_->enabled_ = true;
  Json::Value params(Json::ValueType::objectValue);
  params["key"] = "key3";
  params["value"] = "\"value3\"";

  Json::Value response = RunCommand("WhiteBoard.setSharedData", params);
  EXPECT_EQ(response["error"]["code"].asInt(),
            static_cast<int>(CDPErrorCode::ServerError));
  EXPECT_EQ(response["error"]["message"].asString(),
            "WhiteBoard inspector is unavailable");
}

TEST_F(WhiteBoardInspectorDelegateTest, SetSharedDataRejectsInvalidJson) {
  delegate_->enabled_ = true;
  delegate_->SetInspector(inspector_);
  Json::Value params(Json::ValueType::objectValue);
  params["key"] = "key3";
  params["value"] = "value3";

  Json::Value response = RunCommand("WhiteBoard.setSharedData", params);
  EXPECT_EQ(response["error"]["code"].asInt(),
            static_cast<int>(CDPErrorCode::InvalidParams));
  EXPECT_EQ(response["error"]["message"].asString(),
            "The value must be a valid JSON string!");
}

TEST_F(WhiteBoardInspectorDelegateTest, SetSharedDataRejectsNonStringValue) {
  delegate_->enabled_ = true;
  delegate_->SetInspector(inspector_);
  Json::Value params(Json::ValueType::objectValue);
  params["key"] = "key3";
  params["value"] = 3;

  Json::Value response = RunCommand("WhiteBoard.setSharedData", params);
  EXPECT_EQ(response["error"]["code"].asInt(),
            static_cast<int>(CDPErrorCode::InvalidParams));
  EXPECT_EQ(response["error"]["message"].asString(),
            "params.value: expected string");
}

TEST_F(WhiteBoardInspectorDelegateTest,
       RemoveSharedDataRejectsMissingKeyParam) {
  delegate_->enabled_ = true;
  delegate_->SetInspector(inspector_);

  Json::Value response =
      RunCommand("WhiteBoard.removeSharedData", Json::Value());
  EXPECT_EQ(response["error"]["code"].asInt(),
            static_cast<int>(CDPErrorCode::InvalidParams));
  EXPECT_EQ(response["error"]["message"].asString(),
            "params.key: missing required field");
}

TEST_F(WhiteBoardInspectorDelegateTest, SetSharedDataSucceeds) {
  delegate_->enabled_ = true;
  delegate_->SetInspector(inspector_);
  Json::Value params(Json::ValueType::objectValue);
  params["key"] = "key3";
  params["value"] = "\"value3\"";

  Json::Value response = RunCommand("WhiteBoard.setSharedData", params);
  EXPECT_EQ(response["id"].asInt64(), 123);
  EXPECT_TRUE(response["result"].isObject());
}

TEST_F(WhiteBoardInspectorDelegateTest, GetSharedDataWhenDisabledReturnsError) {
  Json::Value response = RunCommand("WhiteBoard.getSharedData",
                                    Json::Value(Json::ValueType::objectValue));
  EXPECT_EQ(response["error"]["code"].asInt(),
            static_cast<int>(CDPErrorCode::ServerError));
  EXPECT_EQ(response["error"]["message"].asString(),
            "WhiteBoard is not enabled");
}

TEST_F(WhiteBoardInspectorDelegateTest, GetSharedDataReportsBackendFailure) {
  delegate_->enabled_ = true;
  delegate_->SetInspector(inspector_);
  inspector_->white_board_.reset();

  Json::Value response = RunCommand("WhiteBoard.getSharedData",
                                    Json::Value(Json::ValueType::objectValue));
  EXPECT_EQ(response["error"]["code"].asInt(),
            static_cast<int>(CDPErrorCode::ServerError));
  EXPECT_EQ(response["error"]["message"].asString(),
            "Failed to get shared data!");
}

TEST_F(WhiteBoardInspectorDelegateTest, GetSharedDataReturnsEntries) {
  delegate_->enabled_ = true;
  delegate_->SetInspector(inspector_);

  Json::Value response = RunCommand("WhiteBoard.getSharedData",
                                    Json::Value(Json::ValueType::objectValue));
  EXPECT_EQ(response["id"].asInt64(), 123);
  ASSERT_TRUE(response["result"]["entries"].isArray());
  EXPECT_EQ(response["result"]["entries"].size(), 2u);
}

TEST_F(WhiteBoardInspectorDelegateTest,
       RemoveSharedDataWhenDisabledReturnsError) {
  Json::Value params(Json::ValueType::objectValue);
  params["key"] = "key1";

  Json::Value response = RunCommand("WhiteBoard.removeSharedData", params);
  EXPECT_EQ(response["error"]["code"].asInt(),
            static_cast<int>(CDPErrorCode::ServerError));
  EXPECT_EQ(response["error"]["message"].asString(),
            "WhiteBoard is not enabled");
}

TEST_F(WhiteBoardInspectorDelegateTest, RemoveSharedDataRejectsMissingKey) {
  delegate_->enabled_ = true;
  delegate_->SetInspector(inspector_);
  Json::Value params(Json::ValueType::objectValue);
  params["key"] = "key3";

  Json::Value response = RunCommand("WhiteBoard.removeSharedData", params);
  EXPECT_EQ(response["error"]["code"].asInt(),
            static_cast<int>(CDPErrorCode::InvalidParams));
  EXPECT_EQ(response["error"]["message"].asString(), "The key does not exist!");
}

TEST_F(WhiteBoardInspectorDelegateTest, RemoveSharedDataSucceeds) {
  delegate_->enabled_ = true;
  delegate_->SetInspector(inspector_);
  Json::Value params(Json::ValueType::objectValue);
  params["key"] = "key1";

  Json::Value response = RunCommand("WhiteBoard.removeSharedData", params);
  EXPECT_EQ(response["id"].asInt64(), 123);
  EXPECT_TRUE(response["result"].isObject());
}

TEST_F(WhiteBoardInspectorDelegateTest, ClearReportsBackendFailure) {
  delegate_->enabled_ = true;
  delegate_->SetInspector(inspector_);
  inspector_->white_board_.reset();

  Json::Value response =
      RunCommand("WhiteBoard.clear", Json::Value(Json::ValueType::objectValue));
  EXPECT_EQ(response["error"]["code"].asInt(),
            static_cast<int>(CDPErrorCode::ServerError));
  EXPECT_EQ(response["error"]["message"].asString(),
            "Failed to clear shared data!");
}

TEST_F(WhiteBoardInspectorDelegateTest, ClearSucceeds) {
  delegate_->enabled_ = true;
  delegate_->SetInspector(inspector_);

  Json::Value response =
      RunCommand("WhiteBoard.clear", Json::Value(Json::ValueType::objectValue));
  EXPECT_EQ(response["id"].asInt64(), 123);
  EXPECT_TRUE(response["result"].isObject());
}

TEST_F(WhiteBoardInspectorDelegateTest, Notify) {
  std::string key = "key";
  std::string value = "123";
  std::string expected;

  delegate_->OnSharedDataAdded(key, value);
  expected =
      "{\n   \"method\" : \"WhiteBoard.onSharedDataAdded\",\n   \"params\" : "
      "{\n      \"key\" : \"key\",\n      \"value\" : \"123\"\n   }\n}\n";
  EXPECT_EQ(delegate_->event_message_, expected);

  delegate_->OnSharedDataUpdated(key, value);
  expected =
      "{\n   \"method\" : \"WhiteBoard.onSharedDataUpdated\",\n   \"params\" : "
      "{\n      \"key\" : \"key\",\n      \"newValue\" : \"123\"\n   }\n}\n";
  EXPECT_EQ(delegate_->event_message_, expected);

  delegate_->OnSharedDataRemoved(key);
  expected =
      "{\n   \"method\" : \"WhiteBoard.onSharedDataRemoved\",\n   \"params\" : "
      "{\n      \"key\" : \"key\"\n   }\n}\n";
  EXPECT_EQ(delegate_->event_message_, expected);

  delegate_->OnSharedDataCleared();
  expected = "{\n   \"method\" : \"WhiteBoard.onSharedDataCleared\"\n}\n";
  EXPECT_EQ(delegate_->event_message_, expected);
}

// Replays requests through the generated Agent into the real handlers and
// event emitters, and checks every message against the generated CDP
// contract, so changes to either side that break the declared shape fail here.
TEST_F(WhiteBoardInspectorDelegateTest, ResponsesAndEventsMatchContract) {
  std::vector<std::string> problems;
  auto checker = CDPContractChecker::CreateWithGeneratedContract(
      [&problems](const CDPContractViolation& violation) {
        for (const auto& problem : violation.problems) {
          problems.push_back(violation.method + ": " + problem);
        }
      });
  ASSERT_NE(checker, nullptr);
  const void* channel = this;
  int64_t id = 0;
  auto run = [&](const std::string& method, const Json::Value& params) {
    Json::Value request(Json::objectValue);
    request["id"] = static_cast<Json::Int64>(++id);
    request["method"] = method;
    if (!params.isNull()) {
      request["params"] = params;
    }
    checker->OnIncomingMessage(channel, request);
    checker->OnOutgoingMessage(channel, RunCommand(method, params, id));
  };
  auto check_event = [&]() {
    Json::Value event;
    Json::Reader reader;
    ASSERT_TRUE(reader.parse(delegate_->event_message_, event, false));
    checker->OnOutgoingMessage(channel, event);
  };
  Json::Value params(Json::ValueType::objectValue);

  run("WhiteBoard.getSharedData", Json::Value());
  run("WhiteBoard.enable", Json::Value());
  delegate_->SetInspector(inspector_);
  params["key"] = "key3";
  params["value"] = "\"value3\"";
  run("WhiteBoard.setSharedData", params);
  params["value"] = "not json";
  run("WhiteBoard.setSharedData", params);
  run("WhiteBoard.getSharedData", Json::Value());
  params.removeMember("value");
  params["key"] = "key1";
  run("WhiteBoard.removeSharedData", params);
  run("WhiteBoard.clear", Json::Value());
  run("WhiteBoard.disable", Json::Value());

  delegate_->OnSharedDataAdded("key", "1");
  check_event();
  delegate_->OnSharedDataUpdated("key", "2");
  check_event();
  delegate_->OnSharedDataRemoved("key");
  check_event();
  delegate_->OnSharedDataCleared();
  check_event();

  EXPECT_TRUE(problems.empty()) << ::testing::PrintToString(problems);
}

}  // namespace testing
}  // namespace devtool
}  // namespace lynx

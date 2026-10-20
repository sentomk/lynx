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
#include "devtool/lynx_devtool/protocol/contract_test_harness.h"
#include "devtool/testing/mock/white_board_inspector_delegate_mock.h"
#include "third_party/googletest/googletest/include/gtest/gtest.h"

namespace lynx {
namespace devtool {
namespace testing {

class CheckedWhiteBoardDelegate : public WhiteBoardInspectorDelegateMock {
 public:
  explicit CheckedWhiteBoardDelegate(std::shared_ptr<MessageSender> sender)
      : WhiteBoardInspectorDelegateMock(1), sender_(std::move(sender)) {}
  void SendEvent(const Json::Value& value) override {
    WhiteBoardInspectorDelegateMock::SendEvent(value);
    sender_->SendMessage("CDP", value);
  }

 private:
  std::shared_ptr<MessageSender> sender_;
};

class DelegateCommand : public CDPDomainAgentBase {
 public:
  using Handler = void (WhiteBoardInspectorDelegate::*)(
      const std::shared_ptr<CDPResponder>&, const Json::Value&);
  DelegateCommand(WhiteBoardInspectorDelegate& delegate, Handler handler)
      : delegate_(delegate), handler_(handler) {}
  void CallMethod(const std::shared_ptr<CDPResponder>& responder,
                  const Json::Value& request) override {
    (delegate_.*handler_)(responder, request["params"]);
  }

 private:
  WhiteBoardInspectorDelegate& delegate_;
  Handler handler_;
};

class WhiteBoardInspectorDelegateTest : public ::testing::Test {
 public:
  WhiteBoardInspectorDelegateTest() {}
  ~WhiteBoardInspectorDelegateTest() override {}
  void SetUp() override {
    MockReceiver::GetInstance().ResetAll();
    message_sender_ = harness_.Wrap(std::make_shared<MessageSenderMock>());
    delegate_ = std::make_shared<CheckedWhiteBoardDelegate>(message_sender_);
    inspector_ = std::make_shared<WhiteBoardInspectorImpl>();
    white_board_ = std::make_shared<tasm::WhiteBoard>();
    inspector_->SetWhiteBoard(white_board_);
    white_board_->SetInspector(inspector_);

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
  // Runs a delegate command handler through a fresh CDPResponder and returns
  // the parsed CDP envelope the responder emitted.
  Json::Value RunCommand(void (WhiteBoardInspectorDelegate::*method)(
                             const std::shared_ptr<CDPResponder>&,
                             const Json::Value&),
                         const Json::Value& params, int64_t id = 123) {
    MockReceiver::GetInstance().ResetAll();
    Json::Value request(Json::objectValue);
    request["id"] = static_cast<Json::Int64>(id);
    request["params"] = params;
    const std::pair<const char*, DelegateCommand::Handler> handlers[] = {
        {"enable", &WhiteBoardInspectorDelegate::Enable},
        {"disable", &WhiteBoardInspectorDelegate::Disable},
        {"getSharedData", &WhiteBoardInspectorDelegate::GetSharedData},
        {"setSharedData", &WhiteBoardInspectorDelegate::SetSharedData},
        {"removeSharedData", &WhiteBoardInspectorDelegate::RemoveSharedData},
        {"clear", &WhiteBoardInspectorDelegate::Clear}};
    for (const auto& handler : handlers)
      if (handler.second == method)
        request["method"] = std::string("WhiteBoard.") + handler.first;
    DelegateCommand agent(*delegate_, method);
    harness_.Dispatch(agent, message_sender_, request);
    Json::Value response;
    Json::Reader reader;
    reader.parse(MockReceiver::GetInstance().received_message_.second, response,
                 false);
    return response;
  }

  ContractTestHarness harness_;
  std::shared_ptr<WhiteBoardInspectorDelegateMock> delegate_;
  std::shared_ptr<WhiteBoardInspectorImpl> inspector_;
  std::shared_ptr<tasm::WhiteBoard> white_board_;
  std::shared_ptr<MessageSender> message_sender_;
};

TEST_F(WhiteBoardInspectorDelegateTest, Enable) {
  Json::Value response =
      RunCommand(&WhiteBoardInspectorDelegate::Enable, Json::Value(), 123);
  EXPECT_EQ(response["id"].asInt64(), 123);
  EXPECT_TRUE(response["result"].isObject());
  EXPECT_TRUE(response["result"].empty());
  EXPECT_TRUE(delegate_->enabled_);
}

TEST_F(WhiteBoardInspectorDelegateTest, Disable) {
  delegate_->enabled_ = true;
  Json::Value response =
      RunCommand(&WhiteBoardInspectorDelegate::Disable, Json::Value(), 123);
  EXPECT_EQ(response["id"].asInt64(), 123);
  EXPECT_TRUE(response["result"].isObject());
  EXPECT_FALSE(delegate_->enabled_);
}

TEST_F(WhiteBoardInspectorDelegateTest, SetSharedDataWhenDisabledReturnsError) {
  Json::Value params(Json::ValueType::objectValue);
  params["key"] = "key3";
  params["value"] = "value3";

  Json::Value response =
      RunCommand(&WhiteBoardInspectorDelegate::SetSharedData, params);
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

  Json::Value response =
      RunCommand(&WhiteBoardInspectorDelegate::SetSharedData, params);
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

  Json::Value response =
      RunCommand(&WhiteBoardInspectorDelegate::SetSharedData, params);
  EXPECT_EQ(response["error"]["code"].asInt(),
            static_cast<int>(CDPErrorCode::InvalidParams));
  EXPECT_EQ(response["error"]["message"].asString(),
            "The value must be a valid JSON string!");
}

TEST_F(WhiteBoardInspectorDelegateTest, SetSharedDataSucceeds) {
  delegate_->enabled_ = true;
  delegate_->SetInspector(inspector_);
  Json::Value params(Json::ValueType::objectValue);
  params["key"] = "key3";
  params["value"] = "\"value3\"";

  Json::Value response =
      RunCommand(&WhiteBoardInspectorDelegate::SetSharedData, params);
  EXPECT_EQ(response["id"].asInt64(), 123);
  EXPECT_TRUE(response["result"].isObject());
}

TEST_F(WhiteBoardInspectorDelegateTest, GetSharedDataWhenDisabledReturnsError) {
  Json::Value response = RunCommand(&WhiteBoardInspectorDelegate::GetSharedData,
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

  Json::Value response = RunCommand(&WhiteBoardInspectorDelegate::GetSharedData,
                                    Json::Value(Json::ValueType::objectValue));
  EXPECT_EQ(response["error"]["code"].asInt(),
            static_cast<int>(CDPErrorCode::ServerError));
  EXPECT_EQ(response["error"]["message"].asString(),
            "Failed to get shared data!");
}

TEST_F(WhiteBoardInspectorDelegateTest, GetSharedDataReturnsEntries) {
  delegate_->enabled_ = true;
  delegate_->SetInspector(inspector_);

  Json::Value response = RunCommand(&WhiteBoardInspectorDelegate::GetSharedData,
                                    Json::Value(Json::ValueType::objectValue));
  EXPECT_EQ(response["id"].asInt64(), 123);
  ASSERT_TRUE(response["result"]["entries"].isArray());
  EXPECT_EQ(response["result"]["entries"].size(), 2u);
}

TEST_F(WhiteBoardInspectorDelegateTest,
       RemoveSharedDataWhenDisabledReturnsError) {
  Json::Value params(Json::ValueType::objectValue);
  params["key"] = "key1";

  Json::Value response =
      RunCommand(&WhiteBoardInspectorDelegate::RemoveSharedData, params);
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

  Json::Value response =
      RunCommand(&WhiteBoardInspectorDelegate::RemoveSharedData, params);
  EXPECT_EQ(response["error"]["code"].asInt(),
            static_cast<int>(CDPErrorCode::InvalidParams));
  EXPECT_EQ(response["error"]["message"].asString(), "The key does not exist!");
}

TEST_F(WhiteBoardInspectorDelegateTest, RemoveSharedDataSucceeds) {
  delegate_->enabled_ = true;
  delegate_->SetInspector(inspector_);
  Json::Value params(Json::ValueType::objectValue);
  params["key"] = "key1";

  Json::Value response =
      RunCommand(&WhiteBoardInspectorDelegate::RemoveSharedData, params);
  EXPECT_EQ(response["id"].asInt64(), 123);
  EXPECT_TRUE(response["result"].isObject());
}

TEST_F(WhiteBoardInspectorDelegateTest, ClearReportsBackendFailure) {
  delegate_->enabled_ = true;
  delegate_->SetInspector(inspector_);
  inspector_->white_board_.reset();

  Json::Value response = RunCommand(&WhiteBoardInspectorDelegate::Clear,
                                    Json::Value(Json::ValueType::objectValue));
  EXPECT_EQ(response["error"]["code"].asInt(),
            static_cast<int>(CDPErrorCode::ServerError));
  EXPECT_EQ(response["error"]["message"].asString(),
            "Failed to clear shared data!");
}

TEST_F(WhiteBoardInspectorDelegateTest, ClearSucceeds) {
  delegate_->enabled_ = true;
  delegate_->SetInspector(inspector_);

  Json::Value response = RunCommand(&WhiteBoardInspectorDelegate::Clear,
                                    Json::Value(Json::ValueType::objectValue));
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

// Sends requests directly to the real handlers and
// event emitters, and checks every message against the generated CDP
// contract, so changes to either side that break the declared shape fail here.
TEST_F(WhiteBoardInspectorDelegateTest, ResponsesAndEventsMatchContract) {
  int64_t id = 0;
  auto run = [&](const std::string&, auto handler, const Json::Value& params) {
    RunCommand(handler, params, ++id);
  };
  Json::Value params(Json::ValueType::objectValue);

  run("WhiteBoard.getSharedData", &WhiteBoardInspectorDelegate::GetSharedData,
      Json::Value());
  run("WhiteBoard.enable", &WhiteBoardInspectorDelegate::Enable, Json::Value());
  delegate_->SetInspector(inspector_);
  params["key"] = "key3";
  params["value"] = "\"value3\"";
  run("WhiteBoard.setSharedData", &WhiteBoardInspectorDelegate::SetSharedData,
      params);
  params["value"] = "not json";
  run("WhiteBoard.setSharedData", &WhiteBoardInspectorDelegate::SetSharedData,
      params);
  run("WhiteBoard.getSharedData", &WhiteBoardInspectorDelegate::GetSharedData,
      Json::Value());
  params.removeMember("value");
  params["key"] = "key1";
  run("WhiteBoard.removeSharedData",
      &WhiteBoardInspectorDelegate::RemoveSharedData, params);
  run("WhiteBoard.clear", &WhiteBoardInspectorDelegate::Clear, Json::Value());
  run("WhiteBoard.disable", &WhiteBoardInspectorDelegate::Disable,
      Json::Value());

  delegate_->OnSharedDataAdded("key", "1");
  delegate_->OnSharedDataUpdated("key", "2");
  delegate_->OnSharedDataRemoved("key");
  delegate_->OnSharedDataCleared();
}

}  // namespace testing
}  // namespace devtool
}  // namespace lynx

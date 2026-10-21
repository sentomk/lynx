// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.
#ifndef DEVTOOL_TESTING_AGENT_CONTRACT_AGENT_TEST_H_
#define DEVTOOL_TESTING_AGENT_CONTRACT_AGENT_TEST_H_

#include <future>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

#include "base/include/fml/thread.h"
#include "devtool/lynx_devtool/protocol/contract_test_harness.h"
#include "third_party/googletest/googletest/include/gtest/gtest.h"

#define private public
#define protected public
#include "devtool/lynx_devtool/agent/inspector_tasm_executor.h"
#include "devtool/lynx_devtool/agent/inspector_ui_executor.h"
#include "devtool/lynx_devtool/agent/lynx_devtool_mediator.h"
#include "devtool/testing/mock/devtool_platform_facade_mock.h"
#include "devtool/testing/mock/lynx_devtool_ng_mock.h"
#undef private
#undef protected

namespace lynx {
namespace devtool {
namespace testing {
class ContractAgentSender : public MessageSender {
 public:
  void SendMessage(const std::string&, const Json::Value& value) override {
    std::lock_guard<std::mutex> lock(mutex);
    messages.push_back(value);
  }
  void SendMessage(const std::string& type, const std::string& text) override {
    Json::Value value;
    ASSERT_TRUE(Json::Reader().parse(text, value));
    SendMessage(type, value);
  }
  std::mutex mutex;
  std::vector<Json::Value> messages;
};
class ContractAgentTest : public ::testing::Test {
 protected:
  void SetUp() override {
    harness = std::make_unique<ContractTestHarness>();
    sink = std::make_shared<ContractAgentSender>();
    sender = harness->Wrap(sink);
    mediator = std::make_shared<LynxDevToolMediator>();
    devtool = std::make_shared<lynx::testing::LynxDevToolNGMock>();
    devtool->message_sender_ = sender;
    mediator->devtool_wp_ = devtool;
    tasm_thread = std::make_unique<fml::Thread>("contract_tasm");
    ui_thread = std::make_unique<fml::Thread>("contract_ui");
    devtool_thread = std::make_unique<fml::Thread>("contract_devtool");
    mediator->tasm_task_runner_ = tasm_thread->GetTaskRunner();
    mediator->ui_task_runner_ = ui_thread->GetTaskRunner();
    mediator->default_task_runner_ = devtool_thread->GetTaskRunner();
    tasm = std::make_shared<InspectorTasmExecutor>(mediator, nullptr, 1);
    ui = std::make_shared<InspectorUIExecutor>(mediator);
    facade = std::make_shared<lynx::testing::DevToolPlatformFacadeMock>();
    mediator->element_executor_ = tasm;
    mediator->ui_executor_ = ui;
    ui_thread->GetTaskRunner()->PostSyncTask(
        [&] { ui->SetDevToolPlatformFacade(facade); });
  }
  void Drain() {
    for (int i = 0; i < 2; ++i) {
      tasm_thread->GetTaskRunner()->PostSyncTask([] {});
      ui_thread->GetTaskRunner()->PostSyncTask([] {});
      devtool_thread->GetTaskRunner()->PostSyncTask([] {});
    }
  }
  void TearDown() override {
    Drain();
    harness.reset();
    devtool.reset();
    mediator.reset();
    tasm.reset();
    ui.reset();
    tasm_thread.reset();
    ui_thread.reset();
    devtool_thread.reset();
  }
  Json::Value Dispatch(CDPDomainAgentBase& agent, const Json::Value& request) {
    {
      std::lock_guard<std::mutex> lock(sink->mutex);
      sink->messages.clear();
    }
    harness->Dispatch(agent, sender, request);
    Drain();
    Json::Value response;
    std::lock_guard<std::mutex> lock(sink->mutex);
    for (const auto& value : sink->messages) {
      if (value.isMember("id") && value["id"] == request["id"]) {
        EXPECT_TRUE(response.isNull()) << "duplicate response";
        response = value;
      }
    }
    return response;
  }
  Json::Value Request(const std::string& method) {
    Json::Value request(Json::objectValue);
    request["id"] = 1;
    request["method"] = method;
    request["params"] = Json::Value(Json::objectValue);
    return request;
  }
  std::unique_ptr<ContractTestHarness> harness;
  std::shared_ptr<ContractAgentSender> sink;
  std::shared_ptr<MessageSender> sender;
  std::shared_ptr<LynxDevToolMediator> mediator;
  std::shared_ptr<lynx::testing::LynxDevToolNGMock> devtool;
  std::shared_ptr<InspectorTasmExecutor> tasm;
  std::shared_ptr<InspectorUIExecutor> ui;
  std::shared_ptr<lynx::testing::DevToolPlatformFacadeMock> facade;
  std::unique_ptr<fml::Thread> tasm_thread, ui_thread, devtool_thread;
};
}  // namespace testing
}  // namespace devtool
}  // namespace lynx
#endif  // DEVTOOL_TESTING_AGENT_CONTRACT_AGENT_TEST_H_

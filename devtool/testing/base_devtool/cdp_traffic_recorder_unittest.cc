// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#include "devtool/base_devtool/native/cdp_traffic_recorder.h"

#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <thread>
#include <vector>

#include "third_party/googletest/googletest/include/gtest/gtest.h"

namespace lynx {
namespace devtool {
namespace {
void TraceEnv(const std::string& value) {
#ifdef _WIN32
  _putenv_s("LYNX_CDP_TRACE", value.c_str());
#else
  if (value.empty())
    unsetenv("LYNX_CDP_TRACE");
  else
    setenv("LYNX_CDP_TRACE", value.c_str(), 1);
#endif
}
class CDPTrafficRecorderTest : public ::testing::Test {
 protected:
  void SetUp() override {
    auto* current = std::getenv("LYNX_CDP_TRACE");
    saved_env_ = current ? current : "";
    TraceEnv("");
    SetCDPTrafficObserver(nullptr);
    path_ = (std::filesystem::temp_directory_path() /
             ("cdpc-recorder-" +
              std::to_string(
                  std::chrono::steady_clock::now().time_since_epoch().count()) +
              ".jsonl"))
                .string();
  }
  void TearDown() override {
    SetCDPTrafficObserver(nullptr);
    TraceEnv(saved_env_);
    std::filesystem::remove(path_);
  }
  std::vector<Json::Value> Read() {
    std::ifstream file(path_);
    EXPECT_TRUE(file.good());
    std::vector<Json::Value> rows;
    std::string line;
    while (std::getline(file, line)) {
      Json::Value row;
      EXPECT_TRUE(Json::Reader().parse(line, row, false));
      EXPECT_TRUE(row["t"].isInt64());
      EXPECT_GE(row["t"].asInt64(), 1);
      EXPECT_TRUE(row["channel"].asString().find("0x") == 0);
      rows.push_back(std::move(row));
    }
    return rows;
  }
  std::string path_, saved_env_;
};
Json::Value Request(int id = 1) {
  Json::Value request(Json::objectValue);
  request["id"] = id;
  request["method"] = "Lynx.getLogLevel";
  return request;
}
Json::Value Response(int id = 1) {
  Json::Value response(Json::objectValue);
  response["id"] = id;
  response["result"]["runtimeLevel"] = "INFO";
  response["result"]["nativeCompileLevel"] = "MONITOR";
  return response;
}
TEST_F(CDPTrafficRecorderTest, RecordsAndDrainsForPythonVerification) {
  auto recorder = CDPTrafficRecorder::Open(path_);
  ASSERT_NE(recorder, nullptr);
  recorder->OnIncomingMessage(this, Request());
  recorder->OnOutgoingMessage(this, Response());
  recorder->OnIncomingMessage(this, Request(2));
  recorder->OnResponseDropped(this, 2);
  Json::Value fallback(Json::objectValue);
  fallback["id"] = 2;
  fallback["error"]["code"] = -32603;
  fallback["error"]["message"] = "Request was dropped without a reply";
  recorder->OnOutgoingMessage(this, fallback);
  recorder.reset();
  const auto rows = Read();
  ASSERT_EQ(rows.size(), 5u);
  EXPECT_EQ(rows[0]["message"], Request());
  EXPECT_EQ(rows[1]["message"], Response());
  EXPECT_EQ(rows[0]["channel"], rows[1]["channel"]);
  EXPECT_EQ(rows[3]["dir"], "dropped");
  EXPECT_EQ(rows[3]["id"], 2);
  // Intentionally ignored by Git; trace_test consumes this exact C++ output.
  std::filesystem::copy_file(
      path_, std::string(CDPC_TESTDATA_DIR) + "/recorder.trace.jsonl",
      std::filesystem::copy_options::overwrite_existing);
}
TEST_F(CDPTrafficRecorderTest, TruncatesFieldsButKeepsCorrelation) {
  auto recorder = CDPTrafficRecorder::Open(path_, 50);
  ASSERT_NE(recorder, nullptr);
  auto request = Request();
  request["params"]["large"] = std::string(500, 'x');
  recorder->OnIncomingMessage(this, request);
  recorder->OnOutgoingMessage(this, Response());
  recorder.reset();
  const auto rows = Read();
  ASSERT_EQ(rows.size(), 2u);
  EXPECT_TRUE(rows[0]["truncated"].asBool());
  EXPECT_EQ(rows[0]["message"], Request());
  EXPECT_TRUE(rows[1]["truncated"].asBool());
  EXPECT_EQ(rows[1]["message"].size(), 1u);
  EXPECT_EQ(rows[1]["message"]["id"], 1);
}
TEST_F(CDPTrafficRecorderTest, ConcurrentCallbacksWriteCompleteLines) {
  auto recorder = CDPTrafficRecorder::Open(path_);
  ASSERT_NE(recorder, nullptr);
  std::vector<std::thread> threads;
  for (int i = 0; i < 4; ++i) {
    threads.emplace_back([recorder, i] {
      for (int j = 0; j < 50; ++j)
        recorder->OnIncomingMessage(nullptr, Request(i * 50 + j));
    });
  }
  for (auto& thread : threads) thread.join();
  recorder.reset();
  EXPECT_EQ(Read().size(), 200u);
}
TEST_F(CDPTrafficRecorderTest, EnvironmentIsOptInAndDoesNotReplaceObserver) {
  MaybeInstallCDPTrafficRecorderFromEnv();
  EXPECT_FALSE(IsCDPTrafficObserverInstalled());
  auto existing = CDPTrafficRecorder::Open(path_);
  ASSERT_NE(existing, nullptr);
  SetCDPTrafficObserver(existing);
  TraceEnv(path_ + ".unused");
  MaybeInstallCDPTrafficRecorderFromEnv();
  EXPECT_FALSE(std::filesystem::exists(path_ + ".unused"));
  NotifyCDPIncomingMessage(this, "CDP", Json::FastWriter().write(Request()));
  SetCDPTrafficObserver(nullptr);
  existing.reset();
  ASSERT_EQ(Read().size(), 1u);
  TraceEnv(path_);
  MaybeInstallCDPTrafficRecorderFromEnv();
  ASSERT_TRUE(IsCDPTrafficObserverInstalled());
  NotifyCDPOutgoingMessage(this, "CDP", Response());
  SetCDPTrafficObserver(nullptr);
  EXPECT_EQ(Read().size(), 1u);
}
TEST_F(CDPTrafficRecorderTest, CannotOpenMissingDirectory) {
  EXPECT_EQ(CDPTrafficRecorder::Open(path_ + "/trace.jsonl"), nullptr);
}
TEST_F(CDPTrafficRecorderTest, FullQueueCountsDroppedMessagesAtEnd) {
  // A zero-capacity queue deterministically exercises the full-queue path
  // without relying on scheduling or a slow filesystem.
  auto recorder = CDPTrafficRecorder::Open(path_, 1024, 0);
  ASSERT_NE(recorder, nullptr);
  recorder->OnIncomingMessage(this, Request());
  recorder->OnOutgoingMessage(this, Response());
  recorder->OnResponseDropped(this, 2);
  recorder.reset();
  const auto rows = Read();
  ASSERT_EQ(rows.size(), 1u);
  EXPECT_EQ(rows.back()["dir"], "stats");
  EXPECT_EQ(rows.back()["dropped_messages"].asUInt64(), 3u);
}
TEST_F(CDPTrafficRecorderTest, SerializedPayloadPreservesEscaping) {
  auto recorder = CDPTrafficRecorder::Open(path_);
  auto message = Request();
  message["params"]["text"] = "quoted \"value\"\nsecond line\\";
  recorder->OnIncomingMessage(this, message);
  recorder.reset();
  const auto rows = Read();
  ASSERT_EQ(rows.size(), 1u);
  EXPECT_EQ(rows[0]["message"], message);
}
}  // namespace
}  // namespace devtool
}  // namespace lynx

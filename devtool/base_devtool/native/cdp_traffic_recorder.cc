// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#include "devtool/base_devtool/native/cdp_traffic_recorder.h"

#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <deque>
#include <fstream>
#include <mutex>
#include <sstream>
#include <thread>
#include <utility>

#include "base/include/log/logging.h"

namespace lynx {
namespace devtool {
namespace {
Json::Value Envelope(const void* channel, const char* direction) {
  Json::Value row(Json::objectValue);
  row["t"] =
      Json::Int64(std::chrono::duration_cast<std::chrono::milliseconds>(
                      std::chrono::system_clock::now().time_since_epoch())
                      .count());
  row["dir"] = direction;
  std::ostringstream address;
  address << "0x" << std::hex << reinterpret_cast<uintptr_t>(channel);
  row["channel"] = address.str();
  return row;
}
}  // namespace

struct CDPTrafficRecorder::Writer {
  Writer(const std::string& path, size_t max_lines)
      : file(path, std::ios::out | std::ios::trunc),
        max_queued_lines(max_lines) {}
  ~Writer() {
    {
      std::lock_guard<std::mutex> lock(mutex);
      closing = true;
    }
    ready.notify_one();
    if (worker.joinable()) worker.join();
  }
  void Enqueue(std::string line) {
    {
      std::lock_guard<std::mutex> lock(mutex);
      if (lines.size() >= max_queued_lines) {
        if (++dropped_messages == 1)
          LOGW("CDP trace queue full; dropping new records");
        return;
      }
      lines.push_back(std::move(line));
    }
    ready.notify_one();
  }
  void Run() {
    for (;;) {
      std::string line;
      {
        std::unique_lock<std::mutex> lock(mutex);
        ready.wait(lock, [&] { return closing || !lines.empty(); });
        if (lines.empty()) break;
        line = std::move(lines.front());
        lines.pop_front();
      }
      file << line;
      file.flush();
      if (!file && !failed) {
        failed = true;
        LOGW("CDP trace write failed; subsequent records cannot be persisted");
      }
    }
    if (dropped_messages) {
      auto row = Envelope(nullptr, "stats");
      row["dropped_messages"] = Json::UInt64(dropped_messages);
      file << Json::FastWriter().write(row);
      file.flush();
    }
  }
  std::ofstream file;
  std::mutex mutex;
  std::condition_variable ready;
  std::deque<std::string> lines;
  const size_t max_queued_lines;
  uint64_t dropped_messages = 0;
  bool closing = false;
  bool failed = false;
  std::thread worker;
};

std::shared_ptr<CDPTrafficRecorder> CDPTrafficRecorder::Open(
    const std::string& path, size_t max_message_bytes,
    size_t max_queued_lines) {
  auto writer = std::make_unique<Writer>(path, max_queued_lines);
  if (!writer->file) return nullptr;
  writer->worker = std::thread([state = writer.get()] { state->Run(); });
  return std::shared_ptr<CDPTrafficRecorder>(
      new CDPTrafficRecorder(std::move(writer), max_message_bytes));
}
CDPTrafficRecorder::CDPTrafficRecorder(std::unique_ptr<Writer> writer,
                                       size_t max_message_bytes)
    : writer_(std::move(writer)), max_message_bytes_(max_message_bytes) {}
CDPTrafficRecorder::~CDPTrafficRecorder() = default;

void CDPTrafficRecorder::Record(const void* channel, const char* direction,
                                const Json::Value& message) {
  Json::FastWriter json;
  auto row = Envelope(channel, direction);
  std::string payload = json.write(message);
  if (payload.size() > max_message_bytes_) {
    Json::Value metadata(Json::objectValue);
    for (const auto* key : {"method", "id"})
      if (message.isObject() && message.isMember(key))
        metadata[key] = message[key];
    payload = json.write(metadata);
    row["truncated"] = true;
  }
  // FastWriter ends each JSON value with a newline. Reuse the serialized
  // payload verbatim inside the envelope; never serialize the message twice.
  payload.pop_back();
  std::string envelope = json.write(row);
  envelope.resize(envelope.size() -
                  2);  // Remove the closing brace and newline.
  writer_->Enqueue(envelope + ",\"message\":" + payload + "}\n");
}
void CDPTrafficRecorder::OnIncomingMessage(const void* channel,
                                           const Json::Value& message) {
  Record(channel, "in", message);
}
void CDPTrafficRecorder::OnOutgoingMessage(const void* channel,
                                           const Json::Value& message) {
  Record(channel, "out", message);
}
void CDPTrafficRecorder::OnResponseDropped(const void* channel, int64_t id) {
  auto row = Envelope(channel, "dropped");
  row["id"] = Json::Int64(id);
  writer_->Enqueue(Json::FastWriter().write(row));
}
void MaybeInstallCDPTrafficRecorderFromEnv() {
  const char* path = std::getenv("LYNX_CDP_TRACE");
  if (!path || !*path) return;
  if (IsCDPTrafficObserverInstalled()) {
    LOGW("LYNX_CDP_TRACE ignored: a CDP traffic observer is already installed");
    return;
  }
  auto recorder = CDPTrafficRecorder::Open(path);
  if (!recorder) {
    LOGW("Cannot open LYNX_CDP_TRACE output file");
  } else if (!TrySetCDPTrafficObserver(std::move(recorder))) {
    LOGW("LYNX_CDP_TRACE ignored: a CDP traffic observer was installed");
  } else {
    // The observer slot deliberately outlives static destructors. Explicitly
    // drain its writer on normal process exit as well as on observer removal.
    std::atexit([] { SetCDPTrafficObserver(nullptr); });
  }
}
}  // namespace devtool
}  // namespace lynx

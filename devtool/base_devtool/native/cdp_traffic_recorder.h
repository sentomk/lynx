// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#ifndef DEVTOOL_BASE_DEVTOOL_NATIVE_CDP_TRAFFIC_RECORDER_H_
#define DEVTOOL_BASE_DEVTOOL_NATIVE_CDP_TRAFFIC_RECORDER_H_

#include <memory>
#include <string>

#include "devtool/base_devtool/native/public/cdp_traffic_observer.h"

namespace lynx {
namespace devtool {

// Serializes on the caller's thread; a dedicated worker writes and flushes.
// Destruction drains all queued records. No contract data is linked into this.
class CDPTrafficRecorder : public CDPTrafficObserver {
 public:
  static std::shared_ptr<CDPTrafficRecorder> Open(
      const std::string& path, size_t max_message_bytes = 1024 * 1024,
      size_t max_queued_lines = 10000);
  ~CDPTrafficRecorder() override;
  void OnIncomingMessage(const void* channel,
                         const Json::Value& message) override;
  void OnOutgoingMessage(const void* channel,
                         const Json::Value& message) override;
  void OnResponseDropped(const void* channel, int64_t id) override;

 private:
  struct Writer;
  explicit CDPTrafficRecorder(std::unique_ptr<Writer> writer,
                              size_t max_message_bytes);
  void Record(const void* channel, const char* direction,
              const Json::Value& message);
  std::unique_ptr<Writer> writer_;
  const size_t max_message_bytes_;
};

// Called once during DevTool initialization. Never replaces an observer.
void MaybeInstallCDPTrafficRecorderFromEnv();

}  // namespace devtool
}  // namespace lynx
#endif  // DEVTOOL_BASE_DEVTOOL_NATIVE_CDP_TRAFFIC_RECORDER_H_

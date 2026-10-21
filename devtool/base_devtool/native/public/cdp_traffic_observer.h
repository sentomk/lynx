// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#ifndef DEVTOOL_BASE_DEVTOOL_NATIVE_PUBLIC_CDP_TRAFFIC_OBSERVER_H_
#define DEVTOOL_BASE_DEVTOOL_NATIVE_PUBLIC_CDP_TRAFFIC_OBSERVER_H_

#include <cstdint>
#include <memory>
#include <string>

#include "third_party/jsoncpp/include/json/json.h"

namespace lynx {
namespace devtool {

/**
 * CDPTrafficObserver sees every CDP message that crosses a DevTool message
 * channel: requests received from the frontend and responses or events sent
 * back to it. It is a test and debug hook, for example for checking messages
 * against the CDP contract; no observer is installed by default.
 *
 * `channel` identifies the connection. A request and its response are reported
 * with the same channel. Callbacks run on the thread that receives or sends
 * the message, so implementations must be thread safe.
 */
class CDPTrafficObserver {
 public:
  virtual ~CDPTrafficObserver() = default;
  virtual void OnIncomingMessage(const void* channel,
                                 const Json::Value& message) = 0;
  virtual void OnOutgoingMessage(const void* channel,
                                 const Json::Value& message) = 0;
  virtual void OnResponseDropped(const void* channel, int64_t id) {}
};

// Installs the process-wide observer. Pass nullptr to remove it.
void SetCDPTrafficObserver(std::shared_ptr<CDPTrafficObserver> observer);
// Atomically replace the observer and return the previous one for restoration.
std::shared_ptr<CDPTrafficObserver> ExchangeCDPTrafficObserver(
    std::shared_ptr<CDPTrafficObserver> observer);
bool IsCDPTrafficObserverInstalled();
// Atomically install only when the slot is empty.
bool TrySetCDPTrafficObserver(std::shared_ptr<CDPTrafficObserver> observer);
void NotifyCDPResponseDropped(const void* channel, int64_t id);

// Called by message channels. Messages of other types are ignored, and string
// messages are parsed only while an observer is installed.
void NotifyCDPIncomingMessage(const void* channel, const std::string& type,
                              const std::string& message);
void NotifyCDPOutgoingMessage(const void* channel, const std::string& type,
                              const Json::Value& message);
void NotifyCDPOutgoingMessage(const void* channel, const std::string& type,
                              const std::string& message);

}  // namespace devtool
}  // namespace lynx

#endif  // DEVTOOL_BASE_DEVTOOL_NATIVE_PUBLIC_CDP_TRAFFIC_OBSERVER_H_

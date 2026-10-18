// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#include "devtool/base_devtool/native/public/cdp_traffic_observer.h"

#include <atomic>
#include <mutex>
#include <utility>

#include "base/include/no_destructor.h"

namespace lynx {
namespace devtool {

namespace {

constexpr char kCDPMessageType[] = "CDP";

struct ObserverSlot {
  // Checked first so channels pay only an atomic load while no observer is
  // installed.
  std::atomic<bool> installed{false};
  std::mutex mutex;
  std::shared_ptr<CDPTrafficObserver> observer;
};

ObserverSlot& GetSlot() {
  static base::NoDestructor<ObserverSlot> slot;
  return *slot;
}

std::shared_ptr<CDPTrafficObserver> GetObserver(const std::string& type) {
  ObserverSlot& slot = GetSlot();
  if (!slot.installed.load(std::memory_order_acquire) ||
      type != kCDPMessageType) {
    return nullptr;
  }
  std::lock_guard<std::mutex> lock(slot.mutex);
  return slot.observer;
}

bool ParseMessage(const std::string& message, Json::Value* out) {
  Json::Reader reader;
  return reader.parse(message, *out, false) && out->isObject();
}

}  // namespace

void SetCDPTrafficObserver(std::shared_ptr<CDPTrafficObserver> observer) {
  ObserverSlot& slot = GetSlot();
  std::lock_guard<std::mutex> lock(slot.mutex);
  slot.installed.store(observer != nullptr, std::memory_order_release);
  slot.observer = std::move(observer);
}

void NotifyCDPIncomingMessage(const void* channel, const std::string& type,
                              const std::string& message) {
  auto observer = GetObserver(type);
  Json::Value parsed;
  if (observer && ParseMessage(message, &parsed)) {
    observer->OnIncomingMessage(channel, parsed);
  }
}

void NotifyCDPOutgoingMessage(const void* channel, const std::string& type,
                              const Json::Value& message) {
  if (auto observer = GetObserver(type)) {
    observer->OnOutgoingMessage(channel, message);
  }
}

void NotifyCDPOutgoingMessage(const void* channel, const std::string& type,
                              const std::string& message) {
  auto observer = GetObserver(type);
  Json::Value parsed;
  if (observer && ParseMessage(message, &parsed)) {
    observer->OnOutgoingMessage(channel, parsed);
  }
}

}  // namespace devtool
}  // namespace lynx

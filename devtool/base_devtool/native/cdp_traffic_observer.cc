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

std::shared_ptr<CDPTrafficObserver> GetObserver() {
  ObserverSlot& slot = GetSlot();
  if (!slot.installed.load(std::memory_order_acquire)) {
    return nullptr;
  }
  std::lock_guard<std::mutex> lock(slot.mutex);
  return slot.observer;
}

std::shared_ptr<CDPTrafficObserver> GetObserver(const std::string& type) {
  return type == kCDPMessageType ? GetObserver() : nullptr;
}

bool ParseMessage(const std::string& message, Json::Value* out) {
  Json::Reader reader;
  return reader.parse(message, *out, false) && out->isObject();
}

}  // namespace

void SetCDPTrafficObserver(std::shared_ptr<CDPTrafficObserver> observer) {
  ObserverSlot& slot = GetSlot();
  {
    std::lock_guard<std::mutex> lock(slot.mutex);
    slot.observer.swap(observer);
    slot.installed.store(slot.observer != nullptr, std::memory_order_release);
  }
  // An observer may drain its worker on destruction. Do not hold the slot lock.
}

bool IsCDPTrafficObserverInstalled() {
  return GetSlot().installed.load(std::memory_order_acquire);
}

bool TrySetCDPTrafficObserver(std::shared_ptr<CDPTrafficObserver> observer) {
  if (!observer) return false;
  ObserverSlot& slot = GetSlot();
  std::lock_guard<std::mutex> lock(slot.mutex);
  if (slot.observer) return false;
  slot.observer = std::move(observer);
  slot.installed.store(true, std::memory_order_release);
  return true;
}

void NotifyCDPResponseDropped(const void* channel, int64_t id) {
  if (auto observer = GetObserver()) {
    observer->OnResponseDropped(channel, id);
  }
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

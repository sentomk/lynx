// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#ifndef DEVTOOL_BASE_DEVTOOL_NATIVE_PUBLIC_CDP_PARAM_UTILS_H_
#define DEVTOOL_BASE_DEVTOOL_NATIVE_PUBLIC_CDP_PARAM_UTILS_H_

#include <cstddef>
#include <cstdint>
#include <initializer_list>
#include <memory>
#include <string>
#include <string_view>

#include "devtool/base_devtool/native/public/base_devtool_export.h"
#include "third_party/jsoncpp/include/json/json.h"

namespace lynx {
namespace devtool {
class CDPResponder;
class MessageSender;

// Bit sets represent contract unions without weakening either branch.
enum class CDPParamType : uint16_t {
  kInteger = 1 << 0,
  kNumber = 1 << 1,
  kBoolean = 1 << 2,
  kString = 1 << 3,
  kObject = 1 << 4,
  kArray = 1 << 5,
  kAny = 1 << 6,
  kNull = 1 << 7,
  kMap = 1 << 8,
  // Temporary compatibility for handwritten rules during migration.
  kFloat = 1 << 9,
  kCoordinate = 1 << 10,
};
constexpr CDPParamType operator|(CDPParamType a, CDPParamType b) {
  return static_cast<CDPParamType>(static_cast<uint16_t>(a) |
                                   static_cast<uint16_t>(b));
}
struct CDPParamSchema;
struct CDPParamProperty {
  const char* name;
  const CDPParamSchema* schema;
  bool optional;
};
// Constant descriptors generated from parameter contracts only. Pointers allow
// recursive references without embedding the test-only contract JSON.
struct CDPParamSchema {
  CDPParamType types;
  const CDPParamProperty* properties = nullptr;
  size_t property_count = 0;
  const CDPParamSchema* items = nullptr;
  const CDPParamSchema* values = nullptr;
  const CDPParamSchema* ref = nullptr;
  const char* const* enum_values = nullptr;
  size_t enum_count = 0;
};
struct CDPParamRuleSet {
  std::string_view method;
  const CDPParamSchema* params;
};
BASE_DEVTOOL_EXPORT const CDPParamRuleSet* FindCDPParamRules(
    std::string_view method);
BASE_DEVTOOL_EXPORT bool ValidateCDPParams(const Json::Value& params,
                                           const CDPParamRuleSet& rules,
                                           std::string& error);
BASE_DEVTOOL_EXPORT bool CheckCDPParams(
    const std::shared_ptr<MessageSender>& sender, const Json::Value& message,
    std::string_view method);
BASE_DEVTOOL_EXPORT bool CheckCDPParams(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params,
    std::string_view method);

struct CDPParamRule {
  const char* path;
  CDPParamType type;
  bool optional = false;
};

// Temporary handwritten overloads, removed after domain migration.
BASE_DEVTOOL_EXPORT bool ValidateCDPParams(
    const Json::Value& params, std::initializer_list<CDPParamRule> rules,
    std::string& error);
BASE_DEVTOOL_EXPORT bool CheckCDPParams(
    const std::shared_ptr<MessageSender>& sender, const Json::Value& message,
    std::initializer_list<CDPParamRule> rules);
BASE_DEVTOOL_EXPORT bool CheckCDPParams(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params,
    std::initializer_list<CDPParamRule> rules);

// Read a JSON integer representable as int, without coercing other JSON types.
// Both helpers leave result unchanged on failure, including missing/null
// values.
BASE_DEVTOOL_EXPORT bool ReadIntParam(const Json::Value& value, int& result);

// Read a complete decimal integer string (optional sign), representable as int.
// Field-specific constraints such as non-negativity belong to the caller.
BASE_DEVTOOL_EXPORT bool ReadIntStringParam(const Json::Value& value,
                                            int& result);

// Read a finite JSON number as double, without coercing other JSON types.
BASE_DEVTOOL_EXPORT bool ReadFiniteNumberParam(const Json::Value& value,
                                               double& result);

// Read a finite JSON number within float range, without coercing other JSON
// types.
BASE_DEVTOOL_EXPORT bool ReadFiniteFloatParam(const Json::Value& value,
                                              float& result);

}  // namespace devtool
}  // namespace lynx

#endif  // DEVTOOL_BASE_DEVTOOL_NATIVE_PUBLIC_CDP_PARAM_UTILS_H_

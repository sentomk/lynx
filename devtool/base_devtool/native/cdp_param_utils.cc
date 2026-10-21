// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#include "devtool/base_devtool/native/public/cdp_param_utils.h"

#include <cmath>
#include <cstdint>
#include <limits>
#include <string>

#include "base/include/string/string_number_convert.h"
#include "devtool/base_devtool/native/public/cdp_responder.h"

namespace lynx {
namespace devtool {
namespace {
bool CheckType(const Json::Value& value, CDPParamType type) {
  int integer;
  double number;
  float real;
  switch (type) {
    case CDPParamType::kInteger:
      return ReadIntParam(value, integer);
    case CDPParamType::kNumber:
      return ReadFiniteNumberParam(value, number);
    case CDPParamType::kFloat:
      return ReadFiniteFloatParam(value, real);
    case CDPParamType::kCoordinate:
      return ReadFiniteNumberParam(value, number) &&
             number >= std::numeric_limits<int>::min() &&
             number <= std::numeric_limits<int>::max();
    case CDPParamType::kBoolean:
      return value.isBool();
    case CDPParamType::kString:
      return value.isString();
    case CDPParamType::kObject:
      return value.isObject();
    case CDPParamType::kArray:
      return value.isArray();
    case CDPParamType::kAny:
      return true;
  }
  return false;
}
bool CheckPath(const Json::Value& parent, const std::string& remaining,
               const std::string& prefix, const CDPParamRule& rule,
               std::string& error) {
  if (remaining.empty()) {
    if (CheckType(parent, rule.type)) return true;
    error = "Invalid params: incompatible type or numeric range at " + prefix;
    return false;
  }
  const auto split = remaining.find('.');
  const auto part = remaining.substr(0, split);
  const auto tail =
      split == std::string::npos ? "" : remaining.substr(split + 1);
  const bool array =
      part.size() >= 2 && part.compare(part.size() - 2, 2, "[]") == 0;
  const auto name = array ? part.substr(0, part.size() - 2) : part;
  const auto path = prefix + "." + name;
  if (!parent.isObject()) {
    error = "Invalid params: expected object at " + prefix;
    return false;
  }
  if (!parent.isMember(name)) {
    // The explicit parent rule owns requiredness of an optional subtree.
    if (rule.optional || array || !tail.empty()) return true;
    error = "Invalid params: missing " + path;
    return false;
  }
  const auto& value = parent[name];
  if (!array) return CheckPath(value, tail, path, rule, error);
  if (!value.isArray()) {
    error = "Invalid params: expected array at " + path;
    return false;
  }
  for (Json::ArrayIndex i = 0; i < value.size(); ++i) {
    if (!CheckPath(value[i], tail, path + "[" + std::to_string(i) + "]", rule,
                   error))
      return false;
  }
  return true;
}
}  // namespace

bool ValidateCDPParams(const Json::Value& params,
                       std::initializer_list<CDPParamRule> rules,
                       std::string& error) {
  const Json::Value empty(Json::objectValue);
  const auto& value = params.isNull() ? empty : params;
  if (!value.isObject()) {
    error = "Invalid params: expected object";
    return false;
  }
  for (const auto& rule : rules)
    if (!CheckPath(value, rule.path, "params", rule, error)) return false;
  return true;
}
bool CheckCDPParams(const std::shared_ptr<MessageSender>& sender,
                    const Json::Value& message,
                    std::initializer_list<CDPParamRule> rules) {
  std::string error;
  if (ValidateCDPParams(message["params"], rules, error)) return true;
  sender->SendErrorResponse(message["id"].asInt64(),
                            static_cast<int>(CDPErrorCode::InvalidParams),
                            error);
  return false;
}
bool CheckCDPParams(const std::shared_ptr<CDPResponder>& responder,
                    const Json::Value& params,
                    std::initializer_list<CDPParamRule> rules) {
  std::string error;
  if (ValidateCDPParams(params, rules, error)) return true;
  responder->SendError(CDPErrorCode::InvalidParams, error);
  return false;
}

bool ReadIntParam(const Json::Value& value, int& result) {
  if ((value.type() != Json::intValue && value.type() != Json::uintValue) ||
      !value.isInt()) {
    return false;
  }
  result = value.asInt();
  return true;
}

bool ReadIntStringParam(const Json::Value& value, int& result) {
  if (!value.isString()) {
    return false;
  }
  const std::string text = value.asString();
  // Restrict the first byte before StringToInt's character-classification
  // check.
  if (text.empty() ||
      ((text[0] < '0' || text[0] > '9') && text[0] != '+' && text[0] != '-')) {
    return false;
  }
  int64_t parsed = 0;
  if (!base::StringToInt(text, parsed) ||
      parsed < std::numeric_limits<int>::min() ||
      parsed > std::numeric_limits<int>::max()) {
    return false;
  }
  result = static_cast<int>(parsed);
  return true;
}

bool ReadFiniteNumberParam(const Json::Value& value, double& result) {
  if (value.type() != Json::intValue && value.type() != Json::uintValue &&
      value.type() != Json::realValue) {
    return false;
  }
  const double parsed = value.asDouble();
  if (!std::isfinite(parsed)) {
    return false;
  }
  result = parsed;
  return true;
}

bool ReadFiniteFloatParam(const Json::Value& value, float& result) {
  double parsed = 0.0;
  if (!ReadFiniteNumberParam(value, parsed) ||
      parsed < -static_cast<double>(std::numeric_limits<float>::max()) ||
      parsed > static_cast<double>(std::numeric_limits<float>::max())) {
    return false;
  }
  result = static_cast<float>(parsed);
  return true;
}

}  // namespace devtool
}  // namespace lynx

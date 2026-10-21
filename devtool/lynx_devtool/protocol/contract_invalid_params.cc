// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.
#include "devtool/lynx_devtool/protocol/contract_invalid_params.h"

#include <string_view>
#include <vector>

#include "third_party/googletest/googletest/include/gtest/gtest.h"

namespace lynx {
namespace devtool {
namespace protocol {
std::string_view ContractJson();
}
namespace {
const Json::Value& Contract() {
  static const Json::Value value = [] {
    Json::Value parsed;
    const auto json = protocol::ContractJson();
    EXPECT_TRUE(Json::Reader().parse(json.data(), json.data() + json.size(),
                                     parsed, false));
    return parsed;
  }();
  return value;
}
const Json::Value& Resolve(const Json::Value& schema) {
  if (schema.isMember("ref"))
    return Resolve(Contract()["types"][schema["ref"].asString()]);
  return schema;
}
bool Matches(const Json::Value& schema, const Json::Value& value) {
  const auto& type = Resolve(schema)["type"];
  auto match = [&](const std::string& kind) {
    return kind == "any" || (kind == "string" && value.isString()) ||
           (kind == "integer" && value.isIntegral()) ||
           (kind == "number" && value.isNumeric()) ||
           (kind == "boolean" && value.isBool()) ||
           (kind == "object" && value.isObject()) ||
           (kind == "array" && value.isArray()) ||
           (kind == "null" && value.isNull());
  };
  if (type.isString()) return match(type.asString());
  for (const auto& item : type)
    if (match(item.asString())) return true;
  return false;
}
void CheckResponse(const Json::Value& request, const Json::Value& response) {
  ASSERT_TRUE(response.isObject()) << "No response";
  EXPECT_EQ(response["id"], request["id"]);
  ASSERT_TRUE(response["error"].isObject()) << response.toStyledString();
  EXPECT_FALSE(response.isMember("result"));
  EXPECT_EQ(response["error"]["code"], -32602) << response.toStyledString();
}
}  // namespace

void ForEachInvalidParamsVariant(
    const std::string& method, const Json::Value& valid_request,
    const std::function<Json::Value(const Json::Value&)>& run) {
  ASSERT_TRUE(Contract()["methods"].isMember(method)) << method;
  Json::Value base = valid_request;
  bool optional_only = false;
  if (base.isNull()) {
    for (const auto& example : Contract()["examples"]) {
      if (example["request"].isObject() &&
          example["request"]["method"] == method &&
          !example["response"].isMember("error")) {
        base = example["request"];
        break;
      }
    }
    optional_only = base.isNull();
    if (optional_only) base = Json::Value(Json::objectValue);
  }
  base["method"] = method;
  if (!base.isMember("id")) base["id"] = 1;
  if (base["params"].isNull()) base["params"] = Json::Value(Json::objectValue);
  ASSERT_TRUE(base["params"].isObject()) << method;
  const auto& properties =
      Resolve(Contract()["methods"][method]["params"])["properties"];
  const std::vector<Json::Value> candidates = {Json::Value("1"),
                                               Json::Value(Json::objectValue),
                                               Json::Value(Json::arrayValue)};
  auto mutate = [&](const std::string& name, const std::string& child,
                    const Json::Value& schema) {
    for (const auto& value : candidates) {
      if (Matches(schema, value)) continue;
      auto request = base;
      if (child.empty())
        request["params"][name] = value;
      else
        request["params"][name][child] = value;
      SCOPED_TRACE(method + " " + name + (child.empty() ? "" : "." + child) +
                   "=" + value.toStyledString());
      CheckResponse(request, run(request));
    }
  };
  for (const auto& name : properties.getMemberNames()) {
    const auto& prop = properties[name];
    if (optional_only && !prop.get("optional", false).asBool()) continue;
    mutate(name, "", prop["schema"]);
    // One level of nested object members; no recursive fuzzing or side effects.
    const auto& children = Resolve(prop["schema"])["properties"];
    const Json::Value& base_params = base["params"];
    if (base_params[name].isObject() && children.isObject()) {
      for (const auto& child : children.getMemberNames())
        mutate(name, child, children[child]["schema"]);
    }
  }
}
}  // namespace devtool
}  // namespace lynx

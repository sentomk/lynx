// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.
#include "devtool/lynx_devtool/protocol/contract_checker.h"

#include <set>
#include <utility>

namespace lynx {
namespace devtool {
namespace protocol {
std::string_view ContractJson();
}
namespace {
constexpr size_t kMaxPending = 4096;
struct Schema {
  std::string type;
  std::string ref_name;
  const Schema* ref = nullptr;  // Owned by Contract::types, never owns a cycle.
  std::unique_ptr<Schema> items;
  std::map<std::string, std::unique_ptr<Schema>> properties;
  std::set<std::string> optional;
  bool has_properties = false;
  bool has_enum = false;
  std::set<std::string> values;
};

std::unique_ptr<Schema> ParseSchema(const Json::Value& value, int depth = 0) {
  if (!value.isObject() || depth >= 128) return nullptr;
  auto schema = std::make_unique<Schema>();
  if (value.isMember("ref")) {
    if (!value["ref"].isString()) return nullptr;
    schema->ref_name = value["ref"].asString();
    return schema;
  }
  if (!value["type"].isString()) return nullptr;
  schema->type = value["type"].asString();
  static const std::set<std::string> kinds = {
      "string", "integer", "number", "boolean", "object", "array", "any"};
  if (!kinds.count(schema->type)) return nullptr;
  if (value.isMember("enum")) {
    if (schema->type != "string" || !value["enum"].isArray() ||
        value["enum"].empty())
      return nullptr;
    schema->has_enum = true;
    for (const auto& item : value["enum"]) {
      if (!item.isString() || !schema->values.insert(item.asString()).second)
        return nullptr;
    }
  }
  if (schema->type == "array") {
    schema->items = ParseSchema(value["items"], depth + 1);
    if (!schema->items) return nullptr;
  }
  if (value.isMember("properties")) {
    if (schema->type != "object" || !value["properties"].isObject())
      return nullptr;
    schema->has_properties = true;
    for (const auto& name : value["properties"].getMemberNames()) {
      const auto& property = value["properties"][name];
      if (!property.isObject() ||
          (property.isMember("optional") && !property["optional"].isBool()))
        return nullptr;
      auto child = ParseSchema(property["schema"], depth + 1);
      if (!child) return nullptr;
      if (property.get("optional", false).asBool())
        schema->optional.insert(name);
      schema->properties.emplace(name, std::move(child));
    }
  }
  return schema;
}

bool Resolve(Schema* schema,
             const std::map<std::string, std::unique_ptr<Schema>>& types) {
  if (!schema->ref_name.empty()) {
    auto found = types.find(schema->ref_name);
    if (found == types.end()) return false;
    schema->ref = found->second.get();
  }
  if (schema->items && !Resolve(schema->items.get(), types)) return false;
  for (auto& property : schema->properties) {
    if (!Resolve(property.second.get(), types)) return false;
  }
  return true;
}

void Check(const Schema& schema, const Json::Value& value, bool strict,
           const std::string& path, std::vector<std::string>* problems,
           int depth = 0) {
  auto error = [&](const std::string& text) {
    problems->push_back(path + ": " + text);
  };
  if (depth >= 128) {
    error("maximum schema depth exceeded");
    return;
  }
  if (schema.ref) {
    Check(*schema.ref, value, strict, path, problems, depth + 1);
    return;
  }
  const auto& type = schema.type;
  if (type == "any") return;
  const bool valid = (type == "string" && value.isString()) ||
                     (type == "integer" && value.isIntegral()) ||
                     (type == "number" && value.isNumeric()) ||
                     (type == "boolean" && value.isBool()) ||
                     (type == "object" && value.isObject()) ||
                     (type == "array" && value.isArray());
  if (!valid) {
    error("expected " + type);
    return;
  }
  if (schema.has_enum && !schema.values.count(value.asString())) {
    error("'" + value.asString() + "' is not an allowed value");
  }
  if (type == "array") {
    for (Json::ArrayIndex i = 0; i < value.size(); ++i) {
      Check(*schema.items, value[i], strict,
            path + "[" + std::to_string(i) + "]", problems, depth + 1);
    }
  }
  if (schema.has_properties) {
    for (const auto& property : schema.properties) {
      const auto field = path + "." + property.first;
      if (!value.isMember(property.first)) {
        if (!schema.optional.count(property.first))
          problems->push_back(field + ": missing required field");
      } else {
        Check(*property.second, value[property.first], strict, field, problems,
              depth + 1);
      }
    }
    if (strict)
      for (const auto& name : value.getMemberNames()) {
        if (!schema.properties.count(name))
          problems->push_back(path + "." + name + ": unexpected field");
      }
  }
}
const Json::Value& Params(const Json::Value& message) {
  static const Json::Value empty(Json::objectValue);
  return message["params"].isNull() ? empty : message["params"];
}
}  // namespace

struct ContractChecker::Contract {
  struct Method {
    std::unique_ptr<Schema> params;
    std::unique_ptr<Schema> returns;
  };
  std::map<std::string, std::unique_ptr<Schema>> types;
  std::map<std::string, Method> methods;
  std::map<std::string, std::unique_ptr<Schema>> events;
  static std::shared_ptr<const Contract> Parse(std::string_view json) {
    Json::Value root;
    Json::Reader reader;
    if (!reader.parse(json.data(), json.data() + json.size(), root, false) ||
        !root.isObject() || root.get("version", 0) != 1 ||
        !root["types"].isObject() || !root["methods"].isObject() ||
        !root["events"].isObject())
      return nullptr;
    auto contract = std::make_shared<Contract>();
    std::vector<Schema*> all;
    for (const auto& name : root["types"].getMemberNames()) {
      auto schema = ParseSchema(root["types"][name]);
      if (!schema) return nullptr;
      all.push_back(schema.get());
      contract->types.emplace(name, std::move(schema));
    }
    for (const auto& name : root["methods"].getMemberNames()) {
      const auto& entry = root["methods"][name];
      if (!entry.isObject()) return nullptr;
      Method method{ParseSchema(entry["params"]),
                    ParseSchema(entry["returns"])};
      if (!method.params || !method.returns ||
          method.params->type != "object" || method.returns->type != "object")
        return nullptr;
      all.push_back(method.params.get());
      all.push_back(method.returns.get());
      contract->methods.emplace(name, std::move(method));
    }
    for (const auto& name : root["events"].getMemberNames()) {
      if (!root["events"][name].isObject()) return nullptr;
      auto schema = ParseSchema(root["events"][name]["params"]);
      if (!schema || schema->type != "object") return nullptr;
      all.push_back(schema.get());
      contract->events.emplace(name, std::move(schema));
    }
    for (auto schema : all)
      if (!Resolve(schema, contract->types)) return nullptr;
    // Reference-only cycles can never consume a JSON value. Recursive objects
    // can.
    for (const auto& item : contract->types) {
      std::set<const Schema*> chain;
      auto schema = item.second.get();
      while (schema->ref) {
        if (!chain.insert(schema).second) return nullptr;
        schema = const_cast<Schema*>(schema->ref);
      }
    }
    return contract;
  }
};

const char* ContractViolationKindName(ContractViolation::Kind kind) {
#define KIND(name)                    \
  case ContractViolation::Kind::name: \
    return #name
  switch (kind) {
    KIND(kInvalidParams);
    KIND(kInvalidResult);
    KIND(kInvalidError);
    KIND(kInvalidEvent);
    KIND(kUnknownMethod);
    KIND(kUnknownEvent);
    KIND(kDuplicateResponse);
    KIND(kDropped);
    KIND(kUnanswered);
  }
#undef KIND
  return "unknown";
}
std::shared_ptr<ContractChecker> ContractChecker::Create(Reporter reporter) {
  static const auto contract = Contract::Parse(protocol::ContractJson());
  return contract ? std::shared_ptr<ContractChecker>(
                        new ContractChecker(contract, std::move(reporter)))
                  : nullptr;
}
std::shared_ptr<ContractChecker> ContractChecker::Create(std::string_view json,
                                                         Reporter reporter) {
  auto contract = Contract::Parse(json);
  return contract ? std::shared_ptr<ContractChecker>(new ContractChecker(
                        std::move(contract), std::move(reporter)))
                  : nullptr;
}
ContractChecker::ContractChecker(std::shared_ptr<const Contract> contract,
                                 Reporter reporter)
    : contract_(std::move(contract)), reporter_(std::move(reporter)) {}
ContractChecker::~ContractChecker() = default;
void ContractChecker::Report(ContractViolation::Kind kind,
                             const std::string& method, int64_t id,
                             std::vector<std::string> problems) {
  if (reporter_) reporter_({kind, method, id, std::move(problems)});
}
void ContractChecker::OnIncomingMessage(const void* channel,
                                        const Json::Value& message) {
  if (!message.isObject() || !message["id"].isInt64() ||
      !message["method"].isString())
    return;
  const auto id = message["id"].asInt64();
  const auto method = message["method"].asString();
  const Key key{reinterpret_cast<uintptr_t>(channel), id};
  std::vector<ContractViolation> displaced;
  {
    std::lock_guard<std::mutex> lock(mutex_);
    auto previous = pending_.find(key);
    if (previous != pending_.end()) {
      displaced.push_back({ContractViolation::Kind::kUnanswered,
                           previous->second.method,
                           id,
                           {"request id reused before a response"}});
      order_.erase(previous->second.position);
      pending_.erase(previous);
    }
    if (pending_.size() >= kMaxPending) {
      auto oldest = pending_.find(order_.front());
      if (!overflow_reported_) {
        displaced.push_back(
            {ContractViolation::Kind::kUnanswered,
             oldest->second.method,
             oldest->first.second,
             {"pending request limit exceeded; oldest request evicted"}});
        overflow_reported_ = true;
      }
      pending_.erase(oldest);
      order_.pop_front();
    }
    completed_.erase(key);
    order_.push_back(key);
    pending_.emplace(key, Pending{method, std::prev(order_.end()), false});
  }
  for (const auto& violation : displaced)
    if (reporter_) reporter_(violation);
  auto found = contract_->methods.find(method);
  if (found == contract_->methods.end()) {
    Report(ContractViolation::Kind::kUnknownMethod, method, id,
           {"method has no contract"});
    return;
  }
  std::vector<std::string> problems;
  Check(*found->second.params, Params(message), false, "params", &problems);
  if (!problems.empty())
    Report(ContractViolation::Kind::kInvalidParams, method, id,
           std::move(problems));
}
void ContractChecker::OnOutgoingMessage(const void* channel,
                                        const Json::Value& message) {
  if (!message.isObject()) return;
  if (!message.isMember("id")) {
    if (!message["method"].isString()) return;
    const auto method = message["method"].asString();
    auto found = contract_->events.find(method);
    if (found == contract_->events.end()) {
      Report(ContractViolation::Kind::kUnknownEvent, method, 0,
             {"event has no contract"});
      return;
    }
    std::vector<std::string> problems;
    Check(*found->second, Params(message), true, "params", &problems);
    if (!problems.empty())
      Report(ContractViolation::Kind::kInvalidEvent, method, 0,
             std::move(problems));
    return;
  }
  if (!message["id"].isInt64()) return;  // Null ids answer malformed envelopes.
  const auto id = message["id"].asInt64();
  const Key key{reinterpret_cast<uintptr_t>(channel), id};
  std::string method;
  bool matched = false, dropped = false;
  {
    std::lock_guard<std::mutex> lock(mutex_);
    auto pending = pending_.find(key);
    if (pending != pending_.end()) {
      matched = true;
      method = pending->second.method;
      dropped = pending->second.dropped;
      order_.erase(pending->second.position);
      pending_.erase(pending);
      completed_[key] = method;
      if (completed_.size() > kMaxPending) completed_.erase(completed_.begin());
    } else {
      auto completed = completed_.find(key);
      if (completed != completed_.end()) method = completed->second;
    }
  }
  if (!matched) {
    Report(ContractViolation::Kind::kDuplicateResponse, method, id,
           {"response has no outstanding request on this channel"});
    return;
  }
  if (dropped) return;
  const bool has_result = message.isMember("result"),
             has_error = message.isMember("error");
  if (has_result == has_error) {
    Report(ContractViolation::Kind::kInvalidResult, method, id,
           {"response must contain exactly one of result or error"});
    return;
  }
  std::vector<std::string> problems;
  if (has_error) {
    const auto& error = message["error"];
    if (!error.isObject())
      problems.push_back("error: expected object");
    else {
      if (!error.isMember("code"))
        problems.push_back("error.code: missing required field");
      else if (!error["code"].isIntegral())
        problems.push_back("error.code: expected integer");
      if (!error.isMember("message"))
        problems.push_back("error.message: missing required field");
      else if (!error["message"].isString())
        problems.push_back("error.message: expected string");
      for (const auto& name : error.getMemberNames())
        if (name != "code" && name != "message" && name != "data")
          problems.push_back("error." + name + ": unexpected field");
    }
    if (!problems.empty())
      Report(ContractViolation::Kind::kInvalidError, method, id,
             std::move(problems));
  } else {
    auto found = contract_->methods.find(method);
    if (found != contract_->methods.end())
      Check(*found->second.returns, message["result"], true, "result",
            &problems);
    if (!problems.empty())
      Report(ContractViolation::Kind::kInvalidResult, method, id,
             std::move(problems));
  }
}
void ContractChecker::Flush() {
  std::vector<ContractViolation> violations;
  {
    std::lock_guard<std::mutex> lock(mutex_);
    for (const auto& key : order_) {
      const auto& request = pending_.at(key);
      if (!request.dropped)
        violations.push_back({ContractViolation::Kind::kUnanswered,
                              request.method,
                              key.second,
                              {"request has no response"}});
    }
    pending_.clear();
    order_.clear();
    completed_.clear();
    overflow_reported_ = false;
  }
  for (const auto& violation : violations)
    if (reporter_) reporter_(violation);
}
}  // namespace devtool
}  // namespace lynx

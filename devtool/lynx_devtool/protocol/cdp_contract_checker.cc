// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#include "devtool/lynx_devtool/protocol/cdp_contract_checker.h"

#include <unordered_map>
#include <utility>

#include "base/include/log/logging.h"
#include "devtool/lynx_devtool/protocol/cdp_contract_schema.generated.h"

namespace lynx {
namespace devtool {

namespace {

// Bounds the outstanding request table; requests that never get a response
// (for example forwarded ones answered elsewhere) are evicted oldest-key first.
constexpr size_t kMaxPendingRequests = 4096;
// Bounds chains of type references that do not consume any value nesting.
constexpr int kMaxRefChain = 32;

enum KindBit : uint16_t {
  kString = 1 << 0,
  kInteger = 1 << 1,
  kNumber = 1 << 2,
  kBoolean = 1 << 3,
  kObject = 1 << 4,
  kArray = 1 << 5,
  kNull = 1 << 6,
  kAny = 1 << 7,
};

uint16_t KindBitFromName(const std::string& name) {
  static const std::unordered_map<std::string, uint16_t> kNames = {
      {"string", kString},   {"integer", kInteger}, {"number", kNumber},
      {"boolean", kBoolean}, {"object", kObject},   {"array", kArray},
      {"null", kNull},       {"any", kAny},
  };
  auto it = kNames.find(name);
  return it == kNames.end() ? 0 : it->second;
}

uint16_t KindOf(const Json::Value& value) {
  switch (value.type()) {
    case Json::nullValue:
      return kNull;
    case Json::intValue:
    case Json::uintValue:
      return kInteger;
    case Json::realValue:
      return kNumber;
    case Json::stringValue:
      return kString;
    case Json::booleanValue:
      return kBoolean;
    case Json::arrayValue:
      return kArray;
    case Json::objectValue:
      return kObject;
  }
  return 0;
}

std::string KindNames(uint16_t kinds) {
  static const std::pair<uint16_t, const char*> kOrder[] = {
      {kArray, "array"},   {kBoolean, "boolean"}, {kInteger, "integer"},
      {kNull, "null"},     {kNumber, "number"},   {kObject, "object"},
      {kString, "string"}, {kAny, "any"},
  };
  std::string names;
  for (const auto& [bit, name] : kOrder) {
    if (kinds & bit) {
      if (!names.empty()) {
        names += "|";
      }
      names += name;
    }
  }
  return names;
}

struct Schema;
using SchemaPtr = std::shared_ptr<const Schema>;

struct Property {
  SchemaPtr schema;
  bool optional = false;
};

struct Schema {
  uint16_t kinds = 0;
  std::string ref;
  bool has_enum = false;
  std::vector<std::string> enum_values;
  SchemaPtr items;
  // False means an object without declared properties (any object).
  bool has_properties = false;
  std::map<std::string, Property> properties;
  SchemaPtr additional;
};

// Parses the compact schema emitted by `tools/cdpc write`:
// `k` kinds, `r` ref, `e` enum, `i` items, `p` properties (`s` schema,
// `o` optional), `a` map values. A JSON null means "not checked".
bool ParseSchema(const Json::Value& in, SchemaPtr* out) {
  if (in.isNull()) {
    out->reset();
    return true;
  }
  if (!in.isObject()) {
    return false;
  }
  auto schema = std::make_shared<Schema>();
  if (in.isMember("r")) {
    if (!in["r"].isString()) {
      return false;
    }
    schema->ref = in["r"].asString();
    *out = std::move(schema);
    return true;
  }
  const Json::Value& kinds = in["k"];
  if (!kinds.isArray() || kinds.empty()) {
    return false;
  }
  for (const auto& kind : kinds) {
    uint16_t bit = kind.isString() ? KindBitFromName(kind.asString()) : 0;
    if (bit == 0) {
      return false;
    }
    schema->kinds |= bit;
  }
  if (in.isMember("e")) {
    if (!in["e"].isArray()) {
      return false;
    }
    schema->has_enum = true;
    for (const auto& value : in["e"]) {
      schema->enum_values.push_back(value.asString());
    }
  }
  if (in.isMember("i") && !ParseSchema(in["i"], &schema->items)) {
    return false;
  }
  if (in.isMember("p")) {
    const Json::Value& properties = in["p"];
    if (!properties.isObject()) {
      return false;
    }
    schema->has_properties = true;
    for (const auto& name : properties.getMemberNames()) {
      const Json::Value& entry = properties[name];
      Property property;
      if (!entry.isObject() || !ParseSchema(entry["s"], &property.schema) ||
          !property.schema) {
        return false;
      }
      property.optional = entry.isMember("o");
      schema->properties.emplace(name, std::move(property));
    }
  }
  if (in.isMember("a") && !ParseSchema(in["a"], &schema->additional)) {
    return false;
  }
  *out = std::move(schema);
  return true;
}

}  // namespace

struct CDPContractChecker::Contract {
  struct Command {
    SchemaPtr params;
    SchemaPtr result;
    bool params_optional = false;
  };

  std::unordered_map<std::string, SchemaPtr> types;
  std::unordered_map<std::string, Command> commands;
  std::unordered_map<std::string, SchemaPtr> events;

  static std::unique_ptr<Contract> Parse(const std::string& schema_json) {
    Json::Value root;
    Json::Reader reader;
    if (!reader.parse(schema_json, root, false) || !root.isObject()) {
      return nullptr;
    }
    auto contract = std::make_unique<Contract>();
    const Json::Value& types = root["types"];
    for (const auto& name : types.getMemberNames()) {
      if (!ParseSchema(types[name], &contract->types[name])) {
        return nullptr;
      }
    }
    const Json::Value& commands = root["commands"];
    for (const auto& name : commands.getMemberNames()) {
      const Json::Value& entry = commands[name];
      Command& command = contract->commands[name];
      if (!ParseSchema(entry["p"], &command.params) ||
          !ParseSchema(entry["r"], &command.result)) {
        return nullptr;
      }
      command.params_optional = entry.isMember("po");
    }
    const Json::Value& events = root["events"];
    for (const auto& name : events.getMemberNames()) {
      if (!ParseSchema(events[name]["p"], &contract->events[name])) {
        return nullptr;
      }
    }
    return contract;
  }
};

namespace {

class Validator {
 public:
  Validator(const std::unordered_map<std::string, SchemaPtr>& types,
            bool strict, std::vector<std::string>* problems)
      : types_(types), strict_(strict), problems_(problems) {}

  void Check(const Json::Value& value, const Schema& schema,
             const std::string& path, int ref_chain = 0) {
    if (!schema.ref.empty()) {
      auto it = types_.find(schema.ref);
      if (it == types_.end() || !it->second || ref_chain >= kMaxRefChain) {
        problems_->push_back(path + ": unresolved type reference " +
                             schema.ref);
        return;
      }
      Check(value, *it->second, path, ref_chain + 1);
      return;
    }
    if (schema.kinds & kAny) {
      return;
    }
    uint16_t kind = KindOf(value);
    if (!(kind & schema.kinds) &&
        !(kind == kInteger && (schema.kinds & kNumber))) {
      problems_->push_back(path + ": expected " + KindNames(schema.kinds) +
                           ", got " + KindNames(kind));
      return;
    }
    if (kind == kString && schema.has_enum) {
      const std::string text = value.asString();
      bool found = false;
      for (const auto& allowed : schema.enum_values) {
        found = found || allowed == text;
      }
      if (!found) {
        problems_->push_back(path + ": '" + text + "' is not an allowed value");
      }
    } else if (kind == kArray && schema.items) {
      for (Json::ArrayIndex i = 0; i < value.size(); ++i) {
        Check(value[i], *schema.items, path + "[" + std::to_string(i) + "]");
      }
    } else if (kind == kObject) {
      CheckObject(value, schema, path);
    }
  }

 private:
  void CheckObject(const Json::Value& value, const Schema& schema,
                   const std::string& path) {
    if (!schema.has_properties && !schema.additional) {
      return;
    }
    for (const auto& [name, property] : schema.properties) {
      if (!value.isMember(name)) {
        if (!property.optional) {
          problems_->push_back(path + "." + name + ": missing required field");
        }
        continue;
      }
      Check(value[name], *property.schema, path + "." + name);
    }
    for (const auto& name : value.getMemberNames()) {
      if (schema.properties.count(name)) {
        continue;
      }
      if (schema.additional) {
        Check(value[name], *schema.additional, path + "." + name);
      } else if (strict_) {
        problems_->push_back(path + "." + name + ": undeclared field");
      }
    }
  }

  const std::unordered_map<std::string, SchemaPtr>& types_;
  const bool strict_;
  std::vector<std::string>* problems_;
};

const Json::Value& EmptyObject() {
  static const Json::Value kEmpty(Json::objectValue);
  return kEmpty;
}

}  // namespace

const char* CDPContractViolationKindName(CDPContractViolation::Kind kind) {
  switch (kind) {
    case CDPContractViolation::Kind::kInvalidRequest:
      return "invalid request";
    case CDPContractViolation::Kind::kInvalidResult:
      return "invalid result";
    case CDPContractViolation::Kind::kInvalidError:
      return "invalid error";
    case CDPContractViolation::Kind::kInvalidEvent:
      return "invalid event";
    case CDPContractViolation::Kind::kUndeclaredEvent:
      return "undeclared event";
    case CDPContractViolation::Kind::kUnmatchedResponse:
      return "unmatched response";
  }
  return "unknown";
}

std::shared_ptr<CDPContractChecker>
CDPContractChecker::CreateWithGeneratedContract(Reporter reporter) {
  return Create(protocol::GetContractSchemaJson(), std::move(reporter));
}

std::shared_ptr<CDPContractChecker> CDPContractChecker::Create(
    const std::string& schema_json, Reporter reporter) {
  auto contract = Contract::Parse(schema_json);
  if (!contract) {
    LOGE("CDPContractChecker: invalid contract schema");
    return nullptr;
  }
  if (!reporter) {
    reporter = [](const CDPContractViolation& violation) {
      std::string problems;
      for (const auto& problem : violation.problems) {
        problems += "\n  " + problem;
      }
      LOGE("CDP contract violation ("
           << CDPContractViolationKindName(violation.kind) << ") "
           << violation.method << problems);
    };
  }
  return std::shared_ptr<CDPContractChecker>(
      new CDPContractChecker(std::move(contract), std::move(reporter)));
}

CDPContractChecker::CDPContractChecker(std::unique_ptr<Contract> contract,
                                       Reporter reporter)
    : contract_(std::move(contract)), reporter_(std::move(reporter)) {}

CDPContractChecker::~CDPContractChecker() = default;

void CDPContractChecker::OnIncomingMessage(const void* channel,
                                           const Json::Value& message) {
  if (!message.isObject() || !message["method"].isString() ||
      !message["id"].isInt64()) {
    return;
  }
  const std::string method = message["method"].asString();
  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (pending_.size() >= kMaxPendingRequests) {
      pending_.erase(pending_.begin());
    }
    pending_[{channel, message["id"].asInt64()}] = method;
  }

  auto it = contract_->commands.find(method);
  if (it == contract_->commands.end() || !it->second.params) {
    return;
  }
  const Json::Value& params = message["params"];
  if (params.isNull() && it->second.params_optional) {
    return;
  }
  std::vector<std::string> problems;
  Validator(contract_->types, false, &problems)
      .Check(params.isNull() ? EmptyObject() : params, *it->second.params,
             "params");
  if (!problems.empty()) {
    Report(CDPContractViolation::Kind::kInvalidRequest, method,
           std::move(problems));
  }
}

void CDPContractChecker::OnOutgoingMessage(const void* channel,
                                           const Json::Value& message) {
  if (!message.isObject()) {
    return;
  }
  if (message.isMember("id")) {
    CheckResponse(channel, message);
  } else if (message["method"].isString()) {
    CheckEvent(message);
  }
}

void CDPContractChecker::CheckResponse(const void* channel,
                                       const Json::Value& message) {
  const Json::Value& id = message["id"];
  if (!id.isInt64()) {
    // Match the dispatcher's signed id range before calling asInt64().
    // Invalid requests are answered with a null id; nothing to correlate.
    return;
  }
  std::string method;
  {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = pending_.find({channel, id.asInt64()});
    if (it == pending_.end()) {
      method.clear();
    } else {
      method = std::move(it->second);
      pending_.erase(it);
    }
  }
  if (method.empty()) {
    Report(CDPContractViolation::Kind::kUnmatchedResponse, "",
           {"id " + std::to_string(id.asInt64()) +
            " has no outstanding request on this channel"});
    return;
  }

  const bool has_result = message.isMember("result");
  const bool has_error = message.isMember("error");
  if (has_result == has_error) {
    Report(CDPContractViolation::Kind::kInvalidResult, method,
           {"response must contain exactly one of result or error"});
    return;
  }
  if (has_error) {
    const Json::Value& error = message["error"];
    std::vector<std::string> problems;
    if (!error.isObject()) {
      problems.push_back("error: expected object");
    } else {
      if (!error["code"].isIntegral() || error["code"].isBool()) {
        problems.push_back("error.code: expected integer");
      }
      if (!error["message"].isString()) {
        problems.push_back("error.message: expected string");
      }
    }
    if (!problems.empty()) {
      Report(CDPContractViolation::Kind::kInvalidError, method,
             std::move(problems));
    }
    return;
  }

  auto it = contract_->commands.find(method);
  if (it == contract_->commands.end() || !it->second.result) {
    return;
  }
  std::vector<std::string> problems;
  Validator(contract_->types, true, &problems)
      .Check(message["result"], *it->second.result, "result");
  if (!problems.empty()) {
    Report(CDPContractViolation::Kind::kInvalidResult, method,
           std::move(problems));
  }
}

void CDPContractChecker::CheckEvent(const Json::Value& message) {
  const std::string method = message["method"].asString();
  auto it = contract_->events.find(method);
  if (it == contract_->events.end()) {
    Report(CDPContractViolation::Kind::kUndeclaredEvent, method,
           {"event has no contract"});
    return;
  }
  if (!it->second) {
    return;
  }
  const Json::Value& params = message["params"];
  std::vector<std::string> problems;
  Validator(contract_->types, true, &problems)
      .Check(params.isNull() ? EmptyObject() : params, *it->second, "params");
  if (!problems.empty()) {
    Report(CDPContractViolation::Kind::kInvalidEvent, method,
           std::move(problems));
  }
}

void CDPContractChecker::Report(CDPContractViolation::Kind kind,
                                const std::string& method,
                                std::vector<std::string> problems) {
  reporter_(CDPContractViolation{kind, method, std::move(problems)});
}

}  // namespace devtool
}  // namespace lynx

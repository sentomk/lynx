// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#ifndef DEVTOOL_BASE_DEVTOOL_NATIVE_PUBLIC_CDP_TYPED_RESPONDER_H_
#define DEVTOOL_BASE_DEVTOOL_NATIVE_PUBLIC_CDP_TYPED_RESPONDER_H_

#include <memory>
#include <optional>
#include <string>
#include <utility>

#include "devtool/base_devtool/native/public/cdp_error_code.h"
#include "devtool/base_devtool/native/public/cdp_responder.h"
#include "third_party/jsoncpp/include/json/json.h"

namespace lynx {
namespace devtool {
namespace cdp {

// Result type of commands without return fields.
struct Empty {};

/**
 * A typed view of a CDPResponder for generated Backend interfaces.
 *
 * It only constrains the success result to the generated result type and
 * encodes it with the generated Encode overload. Completion, the fallback
 * response and thread rules stay with the underlying CDPResponder; copies of a
 * Responder share it.
 */
template <typename Result>
class Responder {
 public:
  explicit Responder(std::shared_ptr<CDPResponder> responder)
      : responder_(std::move(responder)) {}

  void SendSuccess(const Result& result) const {
    responder_->SendSuccess(Encode(result));
  }

  void SendError(CDPErrorCode code, const std::string& message = "") const {
    responder_->SendError(code, message);
  }

 private:
  std::shared_ptr<CDPResponder> responder_;
};

template <>
class Responder<Empty> {
 public:
  explicit Responder(std::shared_ptr<CDPResponder> responder)
      : responder_(std::move(responder)) {}

  void SendSuccess() const { responder_->SendSuccess(); }

  void SendError(CDPErrorCode code, const std::string& message = "") const {
    responder_->SendError(code, message);
  }

 private:
  std::shared_ptr<CDPResponder> responder_;
};

// The original CDP message for a method that has not been migrated yet.
// The generated Agent does not decode it. Callers forward `message["params"]`
// and `responder` to the old Json handler. Do not decode and then encode back.
struct RawCall {
  std::shared_ptr<CDPResponder> responder;
  Json::Value message;
};

// One old error text. `field` is empty when the failure is on the params value
// itself (`params: expected object`) rather than on `params.<field>`.
struct LegacyError {
  const char* field;
  const char* reason;
  CDPErrorCode code;
  const char* message;
};

inline const char* LegacyProblemReason(const std::string& problem) {
  if (problem == "missing required field") {
    return "missing";
  }
  if (problem.find("not an allowed value") != std::string::npos) {
    return "enum";
  }
  if (problem == "has an unexpected type" ||
      problem.compare(0, 9, "expected ") == 0) {
    return "type";
  }
  return "";
}

// Replaces `message` with a legacy text when `error` matches an entry.
// Leaves both outputs unchanged when nothing matches.
inline void ApplyLegacyError(const std::string& error, const LegacyError* table,
                             size_t count, CDPErrorCode* code,
                             std::string* message) {
  if (table == nullptr || count == 0 || code == nullptr || message == nullptr) {
    return;
  }
  const size_t colon = error.find(": ");
  if (colon == std::string::npos) {
    return;
  }
  const std::string path = error.substr(0, colon);
  const std::string problem = error.substr(colon + 2);
  std::string field;
  if (path == "params") {
    field.clear();
  } else if (path.compare(0, 7, "params.") == 0) {
    field = path.substr(7);
  } else {
    return;
  }
  const char* reason = LegacyProblemReason(problem);
  for (size_t index = 0; index < count; ++index) {
    if (field == table[index].field &&
        std::string(table[index].reason) == reason) {
      *code = table[index].code;
      *message = table[index].message;
      return;
    }
  }
}

// Decodes `params` on Get(), so a Backend can hop threads before validation.
// `Decode` is found by argument-dependent lookup in the params type's namespace.
template <typename Params>
class Deferred {
 public:
  using DefaultsFn = void (*)(Params*);

  Deferred() = default;

  Deferred(Json::Value params, const LegacyError* errors, size_t count,
           DefaultsFn defaults = nullptr)
      : params_(std::move(params)),
        errors_(errors),
        count_(count),
        defaults_(defaults) {}

  template <typename Responder>
  std::optional<Params> Get(const Responder& responder) const {
    Params out{};
    std::string error;
    if (!Decode(params_, &out, &error)) {
      CDPErrorCode code = CDPErrorCode::InvalidParams;
      std::string message = error;
      ApplyLegacyError(error, errors_, count_, &code, &message);
      responder.SendError(code, message);
      return std::nullopt;
    }
    if (defaults_ != nullptr) {
      defaults_(&out);
    }
    return out;
  }

 private:
  Json::Value params_;
  const LegacyError* errors_ = nullptr;
  size_t count_ = 0;
  DefaultsFn defaults_ = nullptr;
};

}  // namespace cdp
}  // namespace devtool
}  // namespace lynx

#endif  // DEVTOOL_BASE_DEVTOOL_NATIVE_PUBLIC_CDP_TYPED_RESPONDER_H_

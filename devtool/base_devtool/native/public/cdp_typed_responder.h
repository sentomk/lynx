// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#ifndef DEVTOOL_BASE_DEVTOOL_NATIVE_PUBLIC_CDP_TYPED_RESPONDER_H_
#define DEVTOOL_BASE_DEVTOOL_NATIVE_PUBLIC_CDP_TYPED_RESPONDER_H_

#include <memory>
#include <string>
#include <utility>

#include "devtool/base_devtool/native/public/cdp_error_code.h"
#include "devtool/base_devtool/native/public/cdp_responder.h"

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

}  // namespace cdp
}  // namespace devtool
}  // namespace lynx

#endif  // DEVTOOL_BASE_DEVTOOL_NATIVE_PUBLIC_CDP_TYPED_RESPONDER_H_

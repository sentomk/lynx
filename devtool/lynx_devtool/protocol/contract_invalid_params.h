// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.
#ifndef DEVTOOL_LYNX_DEVTOOL_PROTOCOL_CONTRACT_INVALID_PARAMS_H_
#define DEVTOOL_LYNX_DEVTOOL_PROTOCOL_CONTRACT_INVALID_PARAMS_H_

#include <functional>
#include <string>

#include "third_party/jsoncpp/include/json/json.h"

namespace lynx {
namespace devtool {
// Test-only: a null valid_request selects the first example, or an empty
// request with only optional fields eligible for mutation when no example
// exists. run must drain asynchronous work and return the response to this
// request.
void ForEachInvalidParamsVariant(
    const std::string& method, const Json::Value& valid_request,
    const std::function<Json::Value(const Json::Value& request)>& run);
}  // namespace devtool
}  // namespace lynx
#endif  // DEVTOOL_LYNX_DEVTOOL_PROTOCOL_CONTRACT_INVALID_PARAMS_H_

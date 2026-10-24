// Copyright 2021 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#include "devtool/lynx_devtool/agent/domain_agent/system_info_agent.h"

#include "devtool/base_devtool/native/public/cdp_responder.h"
#include "devtool/lynx_devtool/agent/lynx_global_devtool_mediator.h"
#include "devtool/lynx_devtool/protocol/generated/cdpc_contracts.generated.h"
#include "devtool/lynx_devtool/protocol/generated/cdpc_dispatch.generated.h"

namespace lynx {
namespace devtool {

namespace {

constexpr cdpc::Method<LynxGlobalDevToolMediator> kMethods[] = {
    {cdpc::methods::kSystemInfoGetInfo,
     &LynxGlobalDevToolMediator::SystemInfoGetInfo},
};
static_assert(cdpc::HasValidBindings(kMethods));

}  // namespace

SystemInfoAgent::SystemInfoAgent() = default;

SystemInfoAgent::~SystemInfoAgent() = default;

void SystemInfoAgent::CallMethod(const std::shared_ptr<CDPResponder>& responder,
                                 const Json::Value& message) {
  DispatchCDPMethod(kMethods, LynxGlobalDevToolMediator::GetInstance(),
                    responder, message);
}

}  // namespace devtool
}  // namespace lynx

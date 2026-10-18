// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#include "devtool/lynx_devtool/agent/domain_agent/inspector_global_props_agent.h"

#include <string>
#include <utility>

#include "devtool/lynx_devtool/agent/inspector_tasm_executor.h"
#include "devtool/lynx_devtool/agent/lynx_devtool_mediator.h"

namespace lynx {
namespace devtool {

namespace global_props = protocol::global_props;

InspectorGlobalPropsAgent::InspectorGlobalPropsAgent(
    const std::shared_ptr<LynxDevToolMediator>& devtool_mediator)
    : devtool_mediator_(devtool_mediator) {}

template <typename Responder, typename Body>
void InspectorGlobalPropsAgent::Run(const Responder& responder, Body body) {
  if (!devtool_mediator_) {
    responder.SendError(CDPErrorCode::ServerError,
                        "GlobalProps target is unavailable");
    return;
  }
  const bool dispatched = devtool_mediator_->RunOnTASMThread(
      [executor = devtool_mediator_->GetTasmExecutor(), responder,
       body = std::move(body)] {
        if (!executor) {
          responder.SendError(CDPErrorCode::ServerError,
                              "GlobalProps target is unavailable");
          return;
        }
        body(*executor);
      });
  if (!dispatched) {
    responder.SendError(CDPErrorCode::ServerError,
                        "GlobalProps target is unavailable");
  }
}

void InspectorGlobalPropsAgent::Enable(
    global_props::EnableResponder responder) {
  Run(responder, [responder](InspectorTasmExecutor& executor) {
    executor.SetGlobalPropsEnabled(true);
    responder.SendSuccess();
  });
}

void InspectorGlobalPropsAgent::Disable(
    global_props::DisableResponder responder) {
  Run(responder, [responder](InspectorTasmExecutor& executor) {
    executor.SetGlobalPropsEnabled(false);
    responder.SendSuccess();
  });
}

void InspectorGlobalPropsAgent::Get(global_props::GetResponder responder) {
  Run(responder, [responder](InspectorTasmExecutor& executor) {
    global_props::GetResult result;
    result.global_props = executor.CopyGlobalProps();
    result.timestamp =
        static_cast<int64_t>(executor.GetLastGlobalPropsTimestamp());
    responder.SendSuccess(result);
  });
}

void InspectorGlobalPropsAgent::Replace(
    global_props::ReplaceResponder responder,
    cdp::Deferred<global_props::ReplaceParams> params) {
  // The runner check happens before Get(), matching the old mediator: a
  // missing TASM runner reports unavailable without looking at params.
  if (!devtool_mediator_) {
    responder.SendError(CDPErrorCode::ServerError,
                        "GlobalProps target is unavailable");
    return;
  }
  const bool dispatched = devtool_mediator_->RunOnTASMThread(
      [executor = devtool_mediator_->GetTasmExecutor(), responder, params]() {
        if (!executor) {
          responder.SendError(CDPErrorCode::ServerError,
                              "GlobalProps target is unavailable");
          return;
        }
        const auto decoded = params.Get(responder);
        if (!decoded) {
          return;
        }
        std::string error;
        if (!executor->ReplaceGlobalProps(decoded->global_props, &error)) {
          responder.SendError(CDPErrorCode::ServerError, error);
          return;
        }
        responder.SendSuccess();
      });
  if (!dispatched) {
    responder.SendError(CDPErrorCode::ServerError,
                        "GlobalProps target is unavailable");
  }
}

}  // namespace devtool
}  // namespace lynx

// Copyright 2025 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#include "devtool/lynx_devtool/agent/domain_agent/inspector_white_board_agent.h"

#include <utility>

#include "devtool/lynx_devtool/agent/lynx_devtool_mediator.h"
#include "devtool/lynx_devtool/shared_data/white_board_inspector_delegate.h"

namespace lynx {
namespace devtool {

namespace white_board = protocol::white_board;

InspectorWhiteBoardAgent::InspectorWhiteBoardAgent(
    const std::shared_ptr<LynxDevToolMediator>& devtool_mediator)
    : devtool_mediator_(devtool_mediator) {}

template <typename Responder>
void InspectorWhiteBoardAgent::Run(
    const Responder& responder,
    std::function<void(WhiteBoardInspectorDelegate&)> call) {
  const bool dispatched =
      devtool_mediator_ &&
      devtool_mediator_->RunOnWhiteBoard(
          [responder,
           call = std::move(call)](WhiteBoardInspectorDelegate* delegate) {
            if (delegate == nullptr) {
              responder.SendError(CDPErrorCode::ServerError,
                                  "WhiteBoard inspector delegate is not "
                                  "attached");
              return;
            }
            call(*delegate);
          });
  if (!dispatched) {
    responder.SendError(CDPErrorCode::ServerError,
                        "No WhiteBoard task runner is available");
  }
}

void InspectorWhiteBoardAgent::Clear(white_board::ClearResponder responder) {
  Run(responder, [responder](WhiteBoardInspectorDelegate& delegate) {
    delegate.Clear(responder);
  });
}

void InspectorWhiteBoardAgent::Disable(
    white_board::DisableResponder responder) {
  Run(responder, [responder](WhiteBoardInspectorDelegate& delegate) {
    delegate.Disable(responder);
  });
}

void InspectorWhiteBoardAgent::Enable(white_board::EnableResponder responder) {
  Run(responder, [responder](WhiteBoardInspectorDelegate& delegate) {
    delegate.Enable(responder);
  });
}

void InspectorWhiteBoardAgent::GetSharedData(
    white_board::GetSharedDataResponder responder) {
  Run(responder, [responder](WhiteBoardInspectorDelegate& delegate) {
    delegate.GetSharedData(responder);
  });
}

void InspectorWhiteBoardAgent::RemoveSharedData(
    white_board::RemoveSharedDataResponder responder,
    white_board::RemoveSharedDataParams params) {
  Run(responder, [responder, params = std::move(params)](
                     WhiteBoardInspectorDelegate& delegate) {
    delegate.RemoveSharedData(responder, params);
  });
}

void InspectorWhiteBoardAgent::SetSharedData(
    white_board::SetSharedDataResponder responder,
    white_board::SetSharedDataParams params) {
  Run(responder, [responder, params = std::move(params)](
                     WhiteBoardInspectorDelegate& delegate) {
    delegate.SetSharedData(responder, params);
  });
}

}  // namespace devtool
}  // namespace lynx

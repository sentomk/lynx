// Copyright 2025 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#ifndef DEVTOOL_LYNX_DEVTOOL_AGENT_DOMAIN_AGENT_INSPECTOR_WHITE_BOARD_AGENT_H_
#define DEVTOOL_LYNX_DEVTOOL_AGENT_DOMAIN_AGENT_INSPECTOR_WHITE_BOARD_AGENT_H_

#include <functional>
#include <memory>

#include "devtool/lynx_devtool/protocol/cdp_protocol.generated.h"

namespace lynx {
namespace devtool {

class LynxDevToolMediator;
class WhiteBoardInspectorDelegate;

// WhiteBoard Backend: runs each command on the delegate's owning thread
// through LynxDevToolMediator. Register it wrapped by
// protocol::white_board::CreateAgent.
class InspectorWhiteBoardAgent : public protocol::white_board::Backend {
 public:
  explicit InspectorWhiteBoardAgent(
      const std::shared_ptr<LynxDevToolMediator>& devtool_mediator);
  ~InspectorWhiteBoardAgent() override = default;

  void Clear(protocol::white_board::ClearResponder responder) override;
  void Disable(protocol::white_board::DisableResponder responder) override;
  void Enable(protocol::white_board::EnableResponder responder) override;
  void GetSharedData(
      protocol::white_board::GetSharedDataResponder responder) override;
  void RemoveSharedData(
      protocol::white_board::RemoveSharedDataResponder responder,
      protocol::white_board::RemoveSharedDataParams params) override;
  void SetSharedData(
      protocol::white_board::SetSharedDataResponder responder,
      protocol::white_board::SetSharedDataParams params) override;

 private:
  // Runs |call| with the delegate on its owning thread. Replies ServerError
  // through |responder| when no thread or delegate is available.
  template <typename Responder>
  void Run(const Responder& responder,
           std::function<void(WhiteBoardInspectorDelegate&)> call);

  const std::shared_ptr<LynxDevToolMediator> devtool_mediator_;
};

}  // namespace devtool
}  // namespace lynx

#endif  // DEVTOOL_LYNX_DEVTOOL_AGENT_DOMAIN_AGENT_INSPECTOR_WHITE_BOARD_AGENT_H_

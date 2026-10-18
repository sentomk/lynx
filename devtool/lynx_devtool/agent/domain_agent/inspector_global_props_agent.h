// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#ifndef DEVTOOL_LYNX_DEVTOOL_AGENT_DOMAIN_AGENT_INSPECTOR_GLOBAL_PROPS_AGENT_H_
#define DEVTOOL_LYNX_DEVTOOL_AGENT_DOMAIN_AGENT_INSPECTOR_GLOBAL_PROPS_AGENT_H_

#include <memory>

#include "devtool/lynx_devtool/protocol/cdp_protocol.generated.h"

namespace lynx {
namespace devtool {

class InspectorTasmExecutor;
class LynxDevToolMediator;

// GlobalProps Backend. Register it wrapped by
// protocol::global_props::CreateAgent. validation is legacy: replace decodes
// after the TASM hop so a missing runner still reports "unavailable" before
// a params error.
class InspectorGlobalPropsAgent : public protocol::global_props::Backend {
 public:
  explicit InspectorGlobalPropsAgent(
      const std::shared_ptr<LynxDevToolMediator>& devtool_mediator);
  ~InspectorGlobalPropsAgent() override = default;

  void Disable(protocol::global_props::DisableResponder responder) override;
  void Enable(protocol::global_props::EnableResponder responder) override;
  void Get(protocol::global_props::GetResponder responder) override;
  void Replace(
      protocol::global_props::ReplaceResponder responder,
      cdp::Deferred<protocol::global_props::ReplaceParams> params) override;

 private:
  // Runs |body| on the TASM thread. Replies "GlobalProps target is
  // unavailable" when that thread has no runner.
  template <typename Responder, typename Body>
  void Run(const Responder& responder, Body body);

  const std::shared_ptr<LynxDevToolMediator> devtool_mediator_;
};

}  // namespace devtool
}  // namespace lynx

#endif  // DEVTOOL_LYNX_DEVTOOL_AGENT_DOMAIN_AGENT_INSPECTOR_GLOBAL_PROPS_AGENT_H_

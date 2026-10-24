// Copyright 2021 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#ifndef DEVTOOL_LYNX_DEVTOOL_AGENT_DOMAIN_AGENT_SYSTEM_INFO_AGENT_H_
#define DEVTOOL_LYNX_DEVTOOL_AGENT_DOMAIN_AGENT_SYSTEM_INFO_AGENT_H_

#include <memory>

#include "devtool/base_devtool/native/public/cdp_domain_agent_base.h"

namespace lynx {
namespace devtool {

class SystemInfoAgent : public CDPDomainAgentBase {
 public:
  SystemInfoAgent();
  ~SystemInfoAgent() override;

  void CallMethod(const std::shared_ptr<CDPResponder>& responder,
                  const Json::Value& message) override;
};

}  // namespace devtool
}  // namespace lynx

#endif  // DEVTOOL_LYNX_DEVTOOL_AGENT_DOMAIN_AGENT_SYSTEM_INFO_AGENT_H_

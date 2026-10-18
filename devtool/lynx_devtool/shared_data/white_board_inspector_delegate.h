// Copyright 2025 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#ifndef DEVTOOL_LYNX_DEVTOOL_SHARED_DATA_WHITE_BOARD_INSPECTOR_DELEGATE_H_
#define DEVTOOL_LYNX_DEVTOOL_SHARED_DATA_WHITE_BOARD_INSPECTOR_DELEGATE_H_

#include <memory>

#include "devtool/lynx_devtool/protocol/cdp_protocol.generated.h"
#include "devtool/lynx_devtool/shared_data/white_board_inspector_impl.h"
#include "third_party/jsoncpp/include/json/json.h"

namespace lynx {
namespace devtool {

class WhiteBoardInspectorImpl;

class WhiteBoardInspectorDelegate {
 public:
  explicit WhiteBoardInspectorDelegate(int view_id);
  virtual ~WhiteBoardInspectorDelegate();

  void SetInspector(const std::shared_ptr<WhiteBoardInspectorImpl>& inspector) {
    inspector_ = inspector;
  }

  bool IsEnabled() { return enabled_; }

  // Handles a WhiteBoard command and produces its response through |responder|.
  // These are the terminal handlers shared by the TASM-executor and
  // JS-debugger paths, so both paths emit identical envelopes. Params arrive
  // decoded by the generated WhiteBoard Agent. Disabled commands report
  // ServerError instead of dropping the request silently.
  void Enable(const protocol::white_board::EnableResponder& responder);
  void Disable(const protocol::white_board::DisableResponder& responder);
  void SetSharedData(
      const protocol::white_board::SetSharedDataResponder& responder,
      const protocol::white_board::SetSharedDataParams& params);
  void GetSharedData(
      const protocol::white_board::GetSharedDataResponder& responder);
  void RemoveSharedData(
      const protocol::white_board::RemoveSharedDataResponder& responder,
      const protocol::white_board::RemoveSharedDataParams& params);
  void Clear(const protocol::white_board::ClearResponder& responder);

  void OnSharedDataAdded(const std::string& key, const std::string& value);
  void OnSharedDataUpdated(const std::string& key, const std::string& value);
  void OnSharedDataRemoved(const std::string& key);
  void OnSharedDataCleared();

  virtual void SendEvent(const Json::Value& msg) = 0;

 protected:
  bool enabled_{false};
  int view_id_;
  std::weak_ptr<WhiteBoardInspectorImpl> inspector_;
};

}  // namespace devtool
}  // namespace lynx

#endif  // DEVTOOL_LYNX_DEVTOOL_SHARED_DATA_WHITE_BOARD_INSPECTOR_DELEGATE_H_

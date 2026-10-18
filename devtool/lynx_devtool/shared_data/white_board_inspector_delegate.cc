// Copyright 2025 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#include "devtool/lynx_devtool/shared_data/white_board_inspector_delegate.h"

#include <utility>

namespace lynx {
namespace devtool {

namespace white_board = protocol::white_board;

WhiteBoardInspectorDelegate::WhiteBoardInspectorDelegate(int view_id)
    : view_id_(view_id) {}

WhiteBoardInspectorDelegate::~WhiteBoardInspectorDelegate() {
  auto sp = inspector_.lock();
  if (sp != nullptr) {
    sp->RemoveDelegate(view_id_);
  }
}

void WhiteBoardInspectorDelegate::Enable(
    const white_board::EnableResponder& responder) {
  enabled_ = true;
  responder.SendSuccess();
}

void WhiteBoardInspectorDelegate::Disable(
    const white_board::DisableResponder& responder) {
  enabled_ = false;
  responder.SendSuccess();
}

void WhiteBoardInspectorDelegate::SetSharedData(
    const white_board::SetSharedDataResponder& responder,
    const white_board::SetSharedDataParams& params) {
  if (!enabled_) {
    responder.SendError(CDPErrorCode::ServerError, "WhiteBoard is not enabled");
    return;
  }
  auto sp = inspector_.lock();
  if (sp == nullptr) {
    responder.SendError(CDPErrorCode::ServerError,
                        "WhiteBoard inspector is unavailable");
    return;
  }
  std::string error_msg;
  auto error_code = sp->SetSharedData(params.key, params.value, error_msg);

  if (error_code.has_value()) {
    responder.SendError(*error_code, error_msg);
    return;
  }

  responder.SendSuccess();
}

void WhiteBoardInspectorDelegate::GetSharedData(
    const white_board::GetSharedDataResponder& responder) {
  if (!enabled_) {
    responder.SendError(CDPErrorCode::ServerError, "WhiteBoard is not enabled");
    return;
  }
  auto sp = inspector_.lock();
  if (sp == nullptr) {
    responder.SendError(CDPErrorCode::ServerError,
                        "WhiteBoard inspector is unavailable");
    return;
  }
  std::vector<std::pair<std::string, std::string>> data;
  std::string error_msg;
  auto error_code = sp->GetSharedData(data, error_msg);

  if (error_code.has_value()) {
    responder.SendError(*error_code, error_msg);
    return;
  }

  white_board::GetSharedDataResult result;
  result.entries.reserve(data.size());
  for (auto& item : data) {
    result.entries.push_back({std::move(item.first), std::move(item.second)});
  }
  responder.SendSuccess(result);
}

void WhiteBoardInspectorDelegate::RemoveSharedData(
    const white_board::RemoveSharedDataResponder& responder,
    const white_board::RemoveSharedDataParams& params) {
  if (!enabled_) {
    responder.SendError(CDPErrorCode::ServerError, "WhiteBoard is not enabled");
    return;
  }
  auto sp = inspector_.lock();
  if (sp == nullptr) {
    responder.SendError(CDPErrorCode::ServerError,
                        "WhiteBoard inspector is unavailable");
    return;
  }
  std::string error_msg;
  auto error_code = sp->RemoveSharedData(params.key, error_msg);

  if (error_code.has_value()) {
    responder.SendError(*error_code, error_msg);
    return;
  }

  responder.SendSuccess();
}

void WhiteBoardInspectorDelegate::Clear(
    const white_board::ClearResponder& responder) {
  if (!enabled_) {
    responder.SendError(CDPErrorCode::ServerError, "WhiteBoard is not enabled");
    return;
  }
  auto sp = inspector_.lock();
  if (sp == nullptr) {
    responder.SendError(CDPErrorCode::ServerError,
                        "WhiteBoard inspector is unavailable");
    return;
  }
  std::string error_msg;
  auto error_code = sp->ClearSharedData(error_msg);

  if (error_code.has_value()) {
    responder.SendError(*error_code, error_msg);
    return;
  }

  responder.SendSuccess();
}

void WhiteBoardInspectorDelegate::OnSharedDataAdded(const std::string& key,
                                                    const std::string& value) {
  SendEvent(white_board::Encode(white_board::OnSharedDataAdded{key, value}));
}

void WhiteBoardInspectorDelegate::OnSharedDataUpdated(
    const std::string& key, const std::string& value) {
  SendEvent(white_board::Encode(white_board::OnSharedDataUpdated{key, value}));
}

void WhiteBoardInspectorDelegate::OnSharedDataRemoved(const std::string& key) {
  SendEvent(white_board::Encode(white_board::OnSharedDataRemoved{key}));
}

void WhiteBoardInspectorDelegate::OnSharedDataCleared() {
  SendEvent(white_board::Encode(white_board::OnSharedDataCleared{}));
}

}  // namespace devtool
}  // namespace lynx

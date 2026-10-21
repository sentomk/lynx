// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.
// The fixture must precede agent headers to expose mediator test state.
// clang-format off
#include "devtool/testing/agent/contract_agent_test.h"
// clang-format on
#define private public
#define protected public
#include "devtool/lynx_devtool/agent/lynx_global_devtool_mediator.h"
#undef private
#undef protected
namespace lynx {
namespace devtool {
namespace testing {
class UnavailableBackendTest : public ContractAgentTest {
 protected:
  void CheckError() {
    ASSERT_EQ(sink->messages.size(), 1u);
    EXPECT_EQ(sink->messages[0]["id"], 1);
    EXPECT_TRUE(sink->messages[0]["error"]["code"].isInt());
    EXPECT_TRUE(sink->messages[0]["error"]["message"].isString());
    EXPECT_FALSE(sink->messages[0].isMember("result"));
    sink->messages.clear();
  }
};
TEST_F(UnavailableBackendTest,
       LegacyHandlersReplyWhenRunnerOrExecutorIsMissing) {
  using Handler = void (LynxDevToolMediator::*)(
      const std::shared_ptr<MessageSender>&, const Json::Value&);
  struct Case {
    const char* name;
    Handler handler;
  };
  const Case cases[] = {
      {"AddRule", &LynxDevToolMediator::AddRule},
      {"CSS_Disable", &LynxDevToolMediator::CSS_Disable},
      {"CSS_Enable", &LynxDevToolMediator::CSS_Enable},
      {"CopyTo", &LynxDevToolMediator::CopyTo},
      {"CreateStyleSheet", &LynxDevToolMediator::CreateStyleSheet},
      {"DOMDisableDomTree", &LynxDevToolMediator::DOMDisableDomTree},
      {"DOMEnableDomTree", &LynxDevToolMediator::DOMEnableDomTree},
      {"DOM_Disable", &LynxDevToolMediator::DOM_Disable},
      {"DOM_Enable", &LynxDevToolMediator::DOM_Enable},
      {"DOM_Focus", &LynxDevToolMediator::DOM_Focus},
      {"DOM_GetBoxModel", &LynxDevToolMediator::DOM_GetBoxModel},
      {"DescribeNode", &LynxDevToolMediator::DescribeNode},
      {"DiscardSearchResults", &LynxDevToolMediator::DiscardSearchResults},
      {"GetAttributes", &LynxDevToolMediator::GetAttributes},
      {"GetBackgroundColors", &LynxDevToolMediator::GetBackgroundColors},
      {"GetComputedStyleForNode",
       &LynxDevToolMediator::GetComputedStyleForNode},
      {"GetDocument", &LynxDevToolMediator::GetDocument},
      {"GetDocumentWithBoxModel",
       &LynxDevToolMediator::GetDocumentWithBoxModel},
      {"GetInlineStylesForNode", &LynxDevToolMediator::GetInlineStylesForNode},
      {"GetLayersForNode", &LynxDevToolMediator::GetLayersForNode},
      {"GetLynxUITree", &LynxDevToolMediator::GetLynxUITree},
      {"GetMatchedStylesForNode",
       &LynxDevToolMediator::GetMatchedStylesForNode},
      {"GetMediaQueries", &LynxDevToolMediator::GetMediaQueries},
      {"GetNodeForLocation", &LynxDevToolMediator::GetNodeForLocation},
      {"GetOriginalNodeIndex", &LynxDevToolMediator::GetOriginalNodeIndex},
      {"GetOriginalNodeSourceInfo",
       &LynxDevToolMediator::GetOriginalNodeSourceInfo},
      {"GetOuterHTML", &LynxDevToolMediator::GetOuterHTML},
      {"GetScreenshot", &LynxDevToolMediator::GetScreenshot},
      {"GetSearchResults", &LynxDevToolMediator::GetSearchResults},
      {"GetStyleSheetText", &LynxDevToolMediator::GetStyleSheetText},
      {"GetUIInfoForNode", &LynxDevToolMediator::GetUIInfoForNode},
      {"InnerText", &LynxDevToolMediator::InnerText},
      {"InspectorDetached", &LynxDevToolMediator::InspectorDetached},
      {"InspectorEnable", &LynxDevToolMediator::InspectorEnable},
      {"LynxGetComponentId", &LynxDevToolMediator::LynxGetComponentId},
      {"LynxGetData", &LynxDevToolMediator::LynxGetData},
      {"LynxGetProperties", &LynxDevToolMediator::LynxGetProperties},
      {"LynxGetRectToWindow", &LynxDevToolMediator::LynxGetRectToWindow},
      {"LynxGetVersion", &LynxDevToolMediator::LynxGetVersion},
      {"LynxGetViewLocationOnScreen",
       &LynxDevToolMediator::LynxGetViewLocationOnScreen},
      {"LynxSendEventToVM", &LynxDevToolMediator::LynxSendEventToVM},
      {"LynxSetTraceMode", &LynxDevToolMediator::LynxSetTraceMode},
      {"LynxTransferData", &LynxDevToolMediator::LynxTransferData},
      {"MarkUndoableState", &LynxDevToolMediator::MarkUndoableState},
      {"MoveTo", &LynxDevToolMediator::MoveTo},
      {"NativeModuleDisable", &LynxDevToolMediator::NativeModuleDisable},
      {"NativeModuleEnable", &LynxDevToolMediator::NativeModuleEnable},
      {"NativeModuleGetRecords", &LynxDevToolMediator::NativeModuleGetRecords},
      {"NetworkDisable", &LynxDevToolMediator::NetworkDisable},
      {"NetworkEnable", &LynxDevToolMediator::NetworkEnable},
      {"NetworkGetRequestPostData",
       &LynxDevToolMediator::NetworkGetRequestPostData},
      {"NetworkGetResponseBody", &LynxDevToolMediator::NetworkGetResponseBody},
      {"PageCanEmulate", &LynxDevToolMediator::PageCanEmulate},
      {"PageCanScreencast", &LynxDevToolMediator::PageCanScreencast},
      {"PageEnable", &LynxDevToolMediator::PageEnable},
      {"PageGetResourceContent", &LynxDevToolMediator::PageGetResourceContent},
      {"PageGetResourceTree", &LynxDevToolMediator::PageGetResourceTree},
      {"PageNavigate", &LynxDevToolMediator::PageNavigate},
      {"PageReload", &LynxDevToolMediator::PageReload},
      {"PerformSearch", &LynxDevToolMediator::PerformSearch},
      {"PerformanceDisable", &LynxDevToolMediator::PerformanceDisable},
      {"PerformanceEnable", &LynxDevToolMediator::PerformanceEnable},
      {"PushNodesByBackendIdsToFrontend",
       &LynxDevToolMediator::PushNodesByBackendIdsToFrontend},
      {"QuerySelector", &LynxDevToolMediator::QuerySelector},
      {"QuerySelectorAll", &LynxDevToolMediator::QuerySelectorAll},
      {"RemoveNode", &LynxDevToolMediator::RemoveNode},
      {"RequestChildNodes", &LynxDevToolMediator::RequestChildNodes},
      {"ScreencastFrameAck", &LynxDevToolMediator::ScreencastFrameAck},
      {"ScrollIntoViewIfNeeded", &LynxDevToolMediator::ScrollIntoViewIfNeeded},
      {"SetAttributesAsText", &LynxDevToolMediator::SetAttributesAsText},
      {"SetInspectedNode", &LynxDevToolMediator::SetInspectedNode},
      {"SetMediaText", &LynxDevToolMediator::SetMediaText},
      {"SetOuterHTML", &LynxDevToolMediator::SetOuterHTML},
      {"SetStyleSheetText", &LynxDevToolMediator::SetStyleSheetText},
      {"SetStyleTexts", &LynxDevToolMediator::SetStyleTexts},
      {"SetSupportsText", &LynxDevToolMediator::SetSupportsText},
      {"SetUIStyle", &LynxDevToolMediator::SetUIStyle},
      {"StartRuleUsageTracking", &LynxDevToolMediator::StartRuleUsageTracking},
      {"StartScreencast", &LynxDevToolMediator::StartScreencast},
      {"StopRuleUsageTracking", &LynxDevToolMediator::StopRuleUsageTracking},
      {"StopScreencast", &LynxDevToolMediator::StopScreencast},
      {"UITree_Disable", &LynxDevToolMediator::UITree_Disable},
      {"UITree_Enable", &LynxDevToolMediator::UITree_Enable},
      {"UpdateRuleUsageTracking",
       &LynxDevToolMediator::UpdateRuleUsageTracking},
      {"getAllPerformanceEntries",
       &LynxDevToolMediator::getAllPerformanceEntries},
      {"getAllTimingInfo", &LynxDevToolMediator::getAllTimingInfo},
  };
  auto request = Request("test");
  mediator->tasm_task_runner_ = nullptr;
  mediator->ui_task_runner_ = nullptr;
  mediator->default_task_runner_ = nullptr;
  for (const auto& test : cases) {
    SCOPED_TRACE(test.name);
    (mediator.get()->*test.handler)(sink, request);
    CheckError();
  }
  mediator->tasm_task_runner_ = tasm_thread->GetTaskRunner();
  mediator->ui_task_runner_ = ui_thread->GetTaskRunner();
  mediator->default_task_runner_ = devtool_thread->GetTaskRunner();
  mediator->element_executor_.reset();
  mediator->ui_executor_.reset();
  mediator->devtool_executor_.reset();
  for (const auto& test : cases) {
    // Native-module handlers have a manager instead of an executor.
    if (std::string(test.name).find("NativeModule") == 0) continue;
    SCOPED_TRACE(test.name);
    (mediator.get()->*test.handler)(sink, request);
    Drain();
    CheckError();
  }
}
TEST_F(UnavailableBackendTest, ResponderHandlersReplyExplicitlyWithoutBackend) {
  using Handler = void (LynxDevToolMediator::*)(
      const std::shared_ptr<CDPResponder>&, const Json::Value&);
  struct Case {
    const char* name;
    Handler handler;
  };
  const Case cases[] = {
      {"AnimationDisable", &LynxDevToolMediator::AnimationDisable},
      {"AnimationEnable", &LynxDevToolMediator::AnimationEnable},
      {"AnimationGetCurrentTime",
       &LynxDevToolMediator::AnimationGetCurrentTime},
      {"AnimationReleaseAnimations",
       &LynxDevToolMediator::AnimationReleaseAnimations},
      {"AnimationSeekAnimations",
       &LynxDevToolMediator::AnimationSeekAnimations},
      {"AnimationSetPaused", &LynxDevToolMediator::AnimationSetPaused},
      {"CompositingReasons", &LynxDevToolMediator::CompositingReasons},
      {"DispatchMouseEvent", &LynxDevToolMediator::DispatchMouseEvent},
      {"EmulateTouchFromMouseEvent",
       &LynxDevToolMediator::EmulateTouchFromMouseEvent},
      {"GlobalPropsDisable", &LynxDevToolMediator::GlobalPropsDisable},
      {"GlobalPropsEnable", &LynxDevToolMediator::GlobalPropsEnable},
      {"GlobalPropsGet", &LynxDevToolMediator::GlobalPropsGet},
      {"GlobalPropsReplace", &LynxDevToolMediator::GlobalPropsReplace},
      {"HideHighlight", &LynxDevToolMediator::HideHighlight},
      {"HighlightNode", &LynxDevToolMediator::HighlightNode},
      {"InsertText", &LynxDevToolMediator::InsertText},
      {"LayerTreeDisable", &LynxDevToolMediator::LayerTreeDisable},
      {"LayerTreeEnable", &LynxDevToolMediator::LayerTreeEnable},
      {"LogClear", &LynxDevToolMediator::LogClear},
      {"LogDisable", &LynxDevToolMediator::LogDisable},
      {"LogEnable", &LynxDevToolMediator::LogEnable},
      {"SynthesizeTapGesture", &LynxDevToolMediator::SynthesizeTapGesture},
      {"TemplateGetTemplateApiInfo",
       &LynxDevToolMediator::TemplateGetTemplateApiInfo},
      {"TemplateGetTemplateData",
       &LynxDevToolMediator::TemplateGetTemplateData},
      {"TemplateGetTemplateJsInfo",
       &LynxDevToolMediator::TemplateGetTemplateJsInfo},
      {"WhiteBoardClear", &LynxDevToolMediator::WhiteBoardClear},
      {"WhiteBoardDisable", &LynxDevToolMediator::WhiteBoardDisable},
      {"WhiteBoardEnable", &LynxDevToolMediator::WhiteBoardEnable},
      {"WhiteBoardGetSharedData",
       &LynxDevToolMediator::WhiteBoardGetSharedData},
      {"WhiteBoardRemoveSharedData",
       &LynxDevToolMediator::WhiteBoardRemoveSharedData},
      {"WhiteBoardSetSharedData",
       &LynxDevToolMediator::WhiteBoardSetSharedData},
  };
  mediator->tasm_task_runner_ = nullptr;
  mediator->ui_task_runner_ = nullptr;
  mediator->js_task_runner_ = nullptr;
  mediator->default_task_runner_ = nullptr;
  for (const auto& test : cases) {
    SCOPED_TRACE(test.name);
    sink->messages.clear();
    auto responder = std::make_shared<CDPResponder>(sink, 1);
    (mediator.get()->*test.handler)(responder, Json::Value(Json::objectValue));
    // Check while the responder is alive: the destructor fallback must not
    // provide the response and must not report kDropped.
    CheckError();
    responder.reset();
    EXPECT_TRUE(sink->messages.empty());
  }
}
TEST_F(UnavailableBackendTest, GlobalLegacyHandlersReplyWithoutTaskRunner) {
  auto& global = LynxGlobalDevToolMediator::GetInstance();
  auto old_ui = global.ui_task_runner_;
  auto old_default = global.default_task_runner_;
  global.ui_task_runner_ = nullptr;
  global.default_task_runner_ = nullptr;
  auto request = Request("test");
  global.RecordingEnd(sink, request);
  CheckError();
  global.ReplayEnd(sink, request);
  CheckError();
  global.TracingStart(sink, request);
  CheckError();
  global.TracingEnd(sink, request);
  CheckError();
  global.SetStartupTracingConfig(sink, request);
  CheckError();
  global.GetStartupTracingConfig(sink, request);
  CheckError();
  global.GetStartupTracingFile(sink, request);
  CheckError();
  global.TakeVMSnapshotByUrl(sink, request);
  CheckError();
  global.ui_task_runner_ = old_ui;
  global.default_task_runner_ = old_default;
}
}  // namespace testing
}  // namespace devtool
}  // namespace lynx

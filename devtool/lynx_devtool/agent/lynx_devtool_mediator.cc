// Copyright 2024 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#include "devtool/lynx_devtool/agent/lynx_devtool_mediator.h"

#include <cstddef>
#include <string>
#include <utility>
#include <vector>

#include "base/include/log/logging.h"
#include "core/renderer/dom/element.h"
#include "core/renderer/dom/element_manager.h"
#include "core/renderer/ui_wrapper/painting/catalyzer.h"
#include "core/renderer/ui_wrapper/painting/painting_context.h"
#include "core/services/replay/replay_controller.h"
#include "devtool/lynx_devtool/agent/hierarchy_observer_impl.h"
#include "devtool/lynx_devtool/agent/inspector_animation_observer_impl.h"
#include "devtool/lynx_devtool/agent/inspector_common_observer_impl.h"
#include "devtool/lynx_devtool/agent/inspector_element_observer_impl.h"
#include "devtool/lynx_devtool/agent/inspector_util.h"
#include "devtool/lynx_devtool/js_debug/js/inspector_java_script_debugger_impl.h"
#include "devtool/lynx_devtool/js_debug/lepus/inspector_lepus_debugger_impl.h"
#include "devtool/lynx_devtool/lynx_devtool_ng.h"
#include "devtool/lynx_devtool/tracing/devtool_trace_event_def.h"

namespace lynx {
namespace devtool {

namespace {

int GenerateViewId() {
  static base::NoDestructor<std::atomic<int>> id{1};
  return (*id)++;
}

template <typename T>
void RunCallbackOnTasmThread(
    const lynx::fml::RefPtr<lynx::fml::TaskRunner>& runner,
    std::function<void(T)> callback, T value) {
  if (!runner || runner->RunsTasksOnCurrentThread()) {
    callback(std::move(value));
    return;
  }
  runner->PostTask(
      [callback = std::move(callback), value = std::move(value)]() mutable {
        callback(std::move(value));
      });
}

}  // namespace

LynxDevToolMediator::LynxDevToolMediator() { view_id_ = GenerateViewId(); }

void LynxDevToolMediator::Init(
    lynx::shell::LynxShell* shell,
    const std::shared_ptr<LynxDevToolNG>& lynx_devtool_ng) {
  devtool_wp_ = lynx_devtool_ng;
  if (ui_executor_) {
    ui_executor_->ResetInputHandler();
  }

  auto* runners = shell->GetRunners();
  tasm::TemplateAssembler* tasm = shell->GetTasm();
  tasm_task_runner_ = runners->GetTASMTaskRunner();
  js_task_runner_ = runners->GetJSTaskRunner();

  // Capture the platform ref during initialization rather than accessing the
  // TASM-owned painting context from subsequent UI-thread queries.
  auto* painting_context = tasm->page_proxy()
                               ->element_manager()
                               ->catalyzer()
                               ->painting_context()
                               ->impl();
  painting_context_ref_ =
      painting_context ? painting_context->GetPlatformRef() : nullptr;

  ui_task_runner_ = runners->GetUITaskRunner();

  // Preserve domain enabled states across reloads.
  bool white_board_enabled = false;
  bool global_props_enabled = false;
  bool animation_enabled = false;
  uint64_t last_global_props_timestamp = 0;
  if (element_executor_ != nullptr) {
    white_board_enabled = element_executor_->IsWhiteBoardEnabled();
    global_props_enabled = element_executor_->IsGlobalPropsEnabled();
    last_global_props_timestamp =
        element_executor_->GetLastGlobalPropsTimestamp();
    animation_enabled = element_executor_->IsAnimationEnabled();
  }
  element_executor_ = std::make_shared<InspectorTasmExecutor>(
      shared_from_this(), tasm, view_id_);
  element_executor_->SetLastGlobalPropsTimestamp(last_global_props_timestamp);
  element_executor_->SetGlobalPropsEnabled(global_props_enabled);
  element_executor_->SetAnimationEnabled(animation_enabled);
  ui_executor_ = std::make_shared<InspectorUIExecutor>(shared_from_this());
  ui_executor_->SetShell(shell);
  if (!devtool_executor_) {
    devtool_executor_ =
        std::make_shared<InspectorDefaultExecutor>(shared_from_this());
  }
  if (fully_initialized_) {
    RunOnDevToolThread([executor = devtool_executor_]() { executor->Reset(); });
  }
  if (js_debugger_ == nullptr) {
    js_debugger_ = std::make_shared<InspectorJavaScriptDebuggerImpl>(
        shared_from_this(), view_id_);
  }
  if (lepus_debugger_ == nullptr) {
    lepus_debugger_ =
        std::make_shared<InspectorLepusDebuggerImpl>(shared_from_this());
    int64_t record_id = reinterpret_cast<int64_t>(shell);
    lepus_debugger_->SetRecordID(record_id);
  }
  if (native_module_record_manager_ == nullptr) {
    native_module_record_manager_ =
        std::make_shared<NativeModuleRecordManager>(shared_from_this());
  }

  // shell set element observer in tasm thread;
  shell->SetInspectorElementObserver(
      std::make_shared<InspectorElementObserverImpl>(element_executor_));
  // Install the Animation CDP observer on the TASM thread.
  shell->SetInspectorAnimationObserver(
      std::make_shared<InspectorAnimationObserverImpl>(element_executor_));
  shell->SetHierarchyObserver(
      std::make_shared<lynx::devtool::HierarchyObserverImpl>(ui_executor_));
  auto runtime_observer = js_debugger_->GetInspectorRuntimeObserver();
  runtime_observer->SetDevToolMediator(shared_from_this());
  shell->SetInspectorRuntimeObserver(runtime_observer);
  auto lepus_observer = lepus_debugger_->GetInspectorLepusObserver();
  // A logic executor skips per-view BTS runtime creation, so the runtime flag
  // should ideally be false. Keep it unchanged for now: hosts also use
  // LynxTemplateRender's enableJSRuntime to gate global events, and changing
  // the propagated runtime flag requires auditing its internal consumers.
  // Enable direct Lepus console forwarding here to fix DevTool without changing
  // those runtime semantics.
  // TODO: Make the runtime flag reflect per-view runtime availability after
  // decoupling global-event delivery and auditing the remaining consumers.
  lepus_observer->SetConsolePostNeeded(
      !shell->IsRuntimeEnabled() || shell->GetPageOptions().HasLogicExecutor());
  lepus_observer->SetDevToolMediator(shared_from_this());
  tasm->SetLepusObserver(lepus_observer);
  auto common_observer =
      std::make_shared<lynx::devtool::InspectorCommonObserverImpl>(
          lynx_devtool_ng->GetCurrentSender(), shared_from_this());
  tasm->SetInspectorCommonObserver(common_observer);
  tasm::replay::ReplayController::SetDevToolObserver(common_observer);

  auto white_board_delegate = tasm->GetWhiteBoardDelegate();
  if (white_board_delegate != nullptr) {
    const auto& inspector_delegate =
        element_executor_->GetWhiteBoardInspectorDelegate();
    InitWhiteBoardInspector(white_board_delegate, inspector_delegate);
    element_executor_->SetWhiteBoardEnabled(white_board_enabled);
  }

  // When using background runtime, `OnAttached()` is called before `Init()`, so
  // `lepus_debugger_` is not initialized at this time. Therefore, we need to
  // call `OnAttached()` again.
  if (attached_) {
    OnAttached();
  }

  fully_initialized_ = true;
}

void LynxDevToolMediator::SetDevToolPlatformFacade(
    const std::shared_ptr<DevToolPlatformFacade>& platform_facade) {
  if (platform_facade) {
    platform_facade->SetPaintingContextRef(painting_context_ref_.lock());
  }
  if (ui_executor_) {
    ui_executor_->SetDevToolPlatformFacade(platform_facade);
  }
  if (js_debugger_) {
    js_debugger_->SetDevToolPlatformFacade(platform_facade);
  }
  if (devtool_executor_) {
    devtool_executor_->SetDevToolPlatformFacade(platform_facade);
  }
  if (lepus_debugger_) {
    lepus_debugger_->SetDevToolPlatformFacade(platform_facade);
  }
  if (element_executor_) {
    element_executor_->SetDevToolPlatformFacade(platform_facade);
  }
}

std::shared_ptr<lynx::runtime::js::InspectorRuntimeObserverNG>
LynxDevToolMediator::InitWhenBackgroundRuntimeCreated(
    const std::string& group_thread_name,
    const std::shared_ptr<LynxDevToolNG>& lynx_devtool_ng) {
  devtool_wp_ = lynx_devtool_ng;
  js_task_runner_ =
      lynx::base::TaskRunnerManufactor::GetJSRunner(group_thread_name);
  if (js_debugger_ == nullptr) {
    js_debugger_ =
        std::make_shared<lynx::devtool::InspectorJavaScriptDebuggerImpl>(
            shared_from_this(), view_id_);
  }
  if (!devtool_executor_) {
    devtool_executor_ =
        std::make_shared<InspectorDefaultExecutor>(shared_from_this());
  }
  if (native_module_record_manager_ == nullptr) {
    native_module_record_manager_ =
        std::make_shared<NativeModuleRecordManager>(shared_from_this());
  }
  auto runtime_observer = js_debugger_->GetInspectorRuntimeObserver();

  runtime_observer->SetDevToolMediator(shared_from_this());
  return runtime_observer;
}

void LynxDevToolMediator::InitWhenMTSRuntimeCreated(
    DevToolPool* devtool_pool,
    const std::shared_ptr<LynxDevToolNG>& lynx_devtool_ng) {
  devtool_wp_ = lynx_devtool_ng;
  if (lepus_debugger_ == nullptr) {
    lepus_debugger_ =
        std::make_shared<InspectorLepusDebuggerImpl>(shared_from_this());
    lepus_debugger_->SetPreExecute(true);
  }
  auto lepus_observer = lepus_debugger_->GetInspectorLepusObserver();
  lepus_observer->SetDevToolMediator(shared_from_this());
  if (devtool_pool != nullptr) {
    devtool_pool->AddLepusObserver(lepus_observer);
  }
  if (!devtool_executor_) {
    devtool_executor_ =
        std::make_shared<InspectorDefaultExecutor>(shared_from_this());
  }
}

void LynxDevToolMediator::UpdateLepusDebugger(
    const std::shared_ptr<InspectorLepusDebuggerImpl>& debugger) {
  lepus_debugger_ = debugger;
  lepus_debugger_->SetPreExecute(false);
}

void LynxDevToolMediator::OnAttached() {
  attached_ = true;
  if (js_debugger_ != nullptr) {
    js_debugger_->OnAttached();
  }
  if (lepus_debugger_ != nullptr) {
    lepus_debugger_->OnAttached();
  }
}

void LynxDevToolMediator::SetTag(const std::string& tag) {
  if (js_debugger_ != nullptr) {
    auto runtime_observer = js_debugger_->GetInspectorRuntimeObserver();
    runtime_observer->SetTag(tag);
  }
}

// DOM protocol
void LynxDevToolMediator::QuerySelector(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->QuerySelector(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::GetAttributes(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->GetAttributes(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::InnerText(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->InnerText(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::QuerySelectorAll(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->QuerySelectorAll(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::DOM_Enable(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->DOM_Enable(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::DOM_Disable(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->DOM_Disable(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::GetDocument(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->GetDocument(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::DescribeNode(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->DescribeNode(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::GetDocumentWithBoxModel(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_, [element_executor = element_executor_,
                                        sender, message]() {
      element_executor->GetDocumentWithBoxModel(sender, message);
    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::RequestChildNodes(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->RequestChildNodes(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::DOM_GetBoxModel(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->DOM_GetBoxModel(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::SetAttributesAsText(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->SetAttributesAsText(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::MarkUndoableState(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->MarkUndoableState(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::PushNodesByBackendIdsToFrontend(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_, [element_executor = element_executor_,
                                        sender, message]() {
      element_executor->PushNodesByBackendIdsToFrontend(sender, message);
    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::RemoveNode(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->RemoveNode(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::MoveTo(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->MoveTo(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::CopyTo(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->CopyTo(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::GetOuterHTML(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->GetOuterHTML(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::SetOuterHTML(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->SetOuterHTML(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::SetInspectedNode(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->SetInspectedNode(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::PerformSearch(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->PerformSearch(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::GetSearchResults(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->GetSearchResults(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::DiscardSearchResults(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->DiscardSearchResults(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::GetOriginalNodeIndex(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->GetOriginalNodeIndex(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::GetOriginalNodeSourceInfo(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_, [element_executor = element_executor_,
                                        sender, message]() {
      element_executor->GetOriginalNodeSourceInfo(sender, message);
    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::GetNodeForLocation(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (ui_task_runner_ && ui_executor_) {
    RunOnTaskRunner(ui_task_runner_,
                    [ui_executor = ui_executor_, sender, message]() {
                      ui_executor->GetNodeForLocation(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "ui_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::ScrollIntoViewIfNeeded(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!element_executor_ ||
      !RunOnTASMThread([sender, message, executor = element_executor_] {
        executor->ScrollIntoViewIfNeeded(sender, message);
      })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::DOM_Focus(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!element_executor_ ||
      !RunOnTASMThread([sender, message, executor = element_executor_] {
        executor->DOM_Focus(sender, message);
      })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::DOMEnableDomTree(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!element_executor_ ||
      !RunOnTASMThread([sender, message, executor = element_executor_] {
        executor->DOMEnableDomTree(sender, message);
      })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::DOMDisableDomTree(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!element_executor_ ||
      !RunOnTASMThread([sender, message, executor = element_executor_] {
        executor->DOMDisableDomTree(sender, message);
      })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::ScrollIntoView(int node_id) {
  RunOnUIThread([executor = ui_executor_, node_id] {
    executor->ScrollIntoView(node_id);
  });
}

void LynxDevToolMediator::Focus(int node_id) {
  RunOnUIThread(
      [executor = ui_executor_, node_id] { executor->Focus(node_id); });
}

void LynxDevToolMediator::PageReload(bool ignore_cache) {
  RunOnUIThread([executor = ui_executor_, ignore_cache] {
    executor->PageReload(ignore_cache);
  });
}

// CSS protocol
void LynxDevToolMediator::CSS_Enable(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->CSS_Enable(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::CSS_Disable(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->CSS_Disable(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::GetMatchedStylesForNode(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_, [element_executor = element_executor_,
                                        sender, message]() {
      element_executor->GetMatchedStylesForNode(sender, message);
    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::GetLayersForNode(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->GetLayersForNode(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::GetComputedStyleForNode(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_, [element_executor = element_executor_,
                                        sender, message]() {
      element_executor->GetComputedStyleForNode(sender, message);
    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::GetInlineStylesForNode(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->GetInlineStylesForNode(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::SetStyleTexts(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->SetStyleTexts(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::GetStyleSheetText(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->GetStyleSheetText(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::GetBackgroundColors(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->GetBackgroundColors(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::SetStyleSheetText(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->SetStyleSheetText(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::CreateStyleSheet(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->CreateStyleSheet(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::AddRule(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->AddRule(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::StartRuleUsageTracking(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->StartRuleUsageTracking(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}
void LynxDevToolMediator::UpdateRuleUsageTracking(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_, [element_executor = element_executor_,
                                        sender, message]() {
      element_executor->UpdateRuleUsageTracking(sender, message);
    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::StopRuleUsageTracking(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->StopRuleUsageTracking(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::GetMediaQueries(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->GetMediaQueries(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::SetMediaText(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->SetMediaText(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::SetSupportsText(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message]() {
                      element_executor->SetSupportsText(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::HighlightNode(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_, [element_executor = element_executor_,
                                        responder, params]() {
      element_executor->HighlightNode(responder, params);
    });
  } else {
    responder->SendError(CDPErrorCode::InternalError,
                         "Overlay backend is unavailable");
  }
}

void LynxDevToolMediator::HideHighlight(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_, [element_executor = element_executor_,
                                        responder, params]() {
      element_executor->HideHighlight(responder, params);
    });
  } else {
    responder->SendError(CDPErrorCode::InternalError,
                         "Overlay backend is unavailable");
  }
}

void LynxDevToolMediator::Destroy() {
  if (ui_executor_) {
    ui_executor_->ResetInputHandler();
  }

  // Must be called before destructing, because in the destructor of
  // InspectorJavaScriptDebuggerImpl, we will post a task to the JS thread by
  // using the weak_ptr of LynxDevToolMediator saved in it.
  if (js_debugger_ != nullptr) {
    js_debugger_->StopDebug();
    js_debugger_.reset();
  }
}

void LynxDevToolMediator::DispatchJSMessage(const Json::Value& message) {
  if (message.isMember(kKeySessionId) && lepus_debugger_ != nullptr) {
    std::string msg = message.toStyledString();
    TRACE_EVENT(LYNX_TRACE_CATEGORY_DEVTOOL,
                LYNX_DEVTOOL_MEDIATOR_DISPATCH_MTS_MESSAGE, "msg", msg);
    lepus_debugger_->DispatchMessage(msg, message[kKeySessionId].asString());
  } else if (js_debugger_ != nullptr) {
    std::string msg = message.toStyledString();
    TRACE_EVENT(LYNX_TRACE_CATEGORY_DEVTOOL,
                LYNX_DEVTOOL_MEDIATOR_DISPATCH_BTS_MESSAGE, "msg", msg);
    js_debugger_->DispatchMessage(msg);
  }
}

void LynxDevToolMediator::UpdateTarget() {
  if (lepus_debugger_ != nullptr) {
    lepus_debugger_->UpdateTarget();
  }
}

void LynxDevToolMediator::SetRuntimeEnableNeeded(bool enable) {
  CHECK_NULL_AND_LOG_RETURN(js_debugger_, "js_debugger_ is null");
  js_debugger_->SetRuntimeEnableNeeded(enable);
}

void LynxDevToolMediator::RunOnJSThread(lynx::base::closure&& closure,
                                        bool run_now) {
  RunOnTaskRunner(js_task_runner_, std::move(closure), run_now);
}

bool LynxDevToolMediator::RunOnTASMThread(lynx::base::closure&& closure,
                                          bool run_now) {
  if (tasm_task_runner_) {
    RunOnTaskRunner(tasm_task_runner_, std::move(closure), run_now);
    return true;
  }
  return false;
}

bool LynxDevToolMediator::RunOnUIThread(lynx::base::closure&& closure,
                                        bool run_now) {
  if (ui_task_runner_) {
    RunOnTaskRunner(ui_task_runner_, std::move(closure), run_now);
    return true;
  }
  return false;
}

bool LynxDevToolMediator::RunOnDevToolThread(lynx::base::closure&& closure,
                                             bool run_now) {
  if (default_task_runner_) {
    RunOnTaskRunner(default_task_runner_, std::move(closure), run_now);
    return true;
  }
  return false;
}

void LynxDevToolMediator::StartScreencast(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (ui_task_runner_ && ui_executor_) {
    RunOnTaskRunner(ui_task_runner_,
                    [ui_executor = ui_executor_, sender, message]() {
                      ui_executor->StartScreencast(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "ui_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::StopScreencast(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (ui_task_runner_ && ui_executor_) {
    RunOnTaskRunner(ui_task_runner_,
                    [ui_executor = ui_executor_, sender, message]() {
                      ui_executor->StopScreencast(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "ui_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::ScreencastFrameAck(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (ui_task_runner_ && ui_executor_) {
    RunOnTaskRunner(ui_task_runner_,
                    [ui_executor = ui_executor_, sender, message] {
                      ui_executor->ScreencastFrameAck(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "ui_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::PageEnable(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (ui_task_runner_ && ui_executor_) {
    RunOnTaskRunner(ui_task_runner_,
                    [ui_executor = ui_executor_, sender, message] {
                      ui_executor->PageEnable(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "ui_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::PageCanEmulate(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (ui_task_runner_ && ui_executor_) {
    RunOnTaskRunner(ui_task_runner_,
                    [ui_executor = ui_executor_, sender, message] {
                      ui_executor->PageCanEmulate(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "ui_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::PageCanScreencast(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (ui_task_runner_ && ui_executor_) {
    RunOnTaskRunner(ui_task_runner_,
                    [ui_executor = ui_executor_, sender, message] {
                      ui_executor->PageCanScreencast(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "ui_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::PageGetResourceContent(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [element_executor = element_executor_, sender, message] {
                      element_executor->PageGetResourceContent(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "tasm_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::PageGetResourceTree(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (ui_task_runner_ && ui_executor_) {
    RunOnTaskRunner(ui_task_runner_,
                    [ui_executor = ui_executor_, sender, message] {
                      ui_executor->PageGetResourceTree(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "ui_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::PageReload(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (ui_task_runner_ && ui_executor_) {
    RunOnTaskRunner(ui_task_runner_,
                    [ui_executor = ui_executor_, sender, message] {
                      ui_executor->PageReload(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "ui_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::PageNavigate(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (ui_task_runner_ && ui_executor_) {
    RunOnTaskRunner(ui_task_runner_,
                    [ui_executor = ui_executor_, sender, message] {
                      ui_executor->PageNavigate(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "ui_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::GetScreenshot(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (ui_task_runner_ && ui_executor_) {
    RunOnTaskRunner(ui_task_runner_,
                    [ui_executor = ui_executor_, sender, message] {
                      ui_executor->GetScreenshot(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "ui_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::UITree_Enable(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (ui_task_runner_ && ui_executor_) {
    RunOnTaskRunner(ui_task_runner_,
                    [ui_executor = ui_executor_, sender, message] {
                      ui_executor->UITree_Enable(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "ui_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::UITree_Disable(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (ui_task_runner_ && ui_executor_) {
    RunOnTaskRunner(ui_task_runner_,
                    [ui_executor = ui_executor_, sender, message] {
                      ui_executor->UITree_Disable(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "ui_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::GetLynxUITree(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (ui_task_runner_ && ui_executor_) {
    RunOnTaskRunner(ui_task_runner_,
                    [ui_executor = ui_executor_, sender, message] {
                      ui_executor->GetLynxUITree(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "ui_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::GetUIInfoForNode(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (ui_task_runner_ && ui_executor_) {
    RunOnTaskRunner(ui_task_runner_,
                    [ui_executor = ui_executor_, sender, message] {
                      ui_executor->GetUIInfoForNode(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "ui_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::SetUIStyle(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (ui_task_runner_ && ui_executor_) {
    RunOnTaskRunner(ui_task_runner_,
                    [ui_executor = ui_executor_, sender, message] {
                      ui_executor->SetUIStyle(sender, message);
                    });
  } else {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "ui_task_runner_ is unavailable");
  }
}

void LynxDevToolMediator::GlobalPropsEnable(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (!element_executor_ ||
      !RunOnTASMThread([executor = element_executor_, responder, params] {
        executor->GlobalPropsEnable(responder, params);
      })) {
    responder->SendError(CDPErrorCode::ServerError,
                         "GlobalProps target is unavailable");
  }
}

void LynxDevToolMediator::GlobalPropsDisable(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (!element_executor_ ||
      !RunOnTASMThread([executor = element_executor_, responder, params] {
        executor->GlobalPropsDisable(responder, params);
      })) {
    responder->SendError(CDPErrorCode::ServerError,
                         "GlobalProps target is unavailable");
  }
}

void LynxDevToolMediator::GlobalPropsGet(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (!element_executor_ ||
      !RunOnTASMThread([executor = element_executor_, responder, params] {
        executor->GlobalPropsGet(responder, params);
      })) {
    responder->SendError(CDPErrorCode::ServerError,
                         "GlobalProps target is unavailable");
  }
}

void LynxDevToolMediator::GlobalPropsReplace(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (!element_executor_ ||
      !RunOnTASMThread([executor = element_executor_, responder, params] {
        executor->GlobalPropsReplace(responder, params);
      })) {
    responder->SendError(CDPErrorCode::ServerError,
                         "GlobalProps target is unavailable");
  }
}

void LynxDevToolMediator::GlobalPropsChanged() {
  if (tasm_task_runner_) {
    RunOnTaskRunner(tasm_task_runner_, [executor = element_executor_] {
      executor->GlobalPropsChanged();
    });
  }
}

void LynxDevToolMediator::WhiteBoardEnable(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [executor = element_executor_, responder, params] {
                      executor->WhiteBoardEnable(responder, params);
                    });
  } else if (js_task_runner_ && js_debugger_) {
    RunOnJSThread([js_debugger = js_debugger_, responder, params] {
      js_debugger->WhiteBoardEnable(responder, params);
    });
  } else {
    responder->SendError(CDPErrorCode::InternalError,
                         "WhiteBoard backend is unavailable");
  }
}

void LynxDevToolMediator::WhiteBoardDisable(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [executor = element_executor_, responder, params] {
                      executor->WhiteBoardDisable(responder, params);
                    });
  } else if (js_task_runner_ && js_debugger_) {
    RunOnJSThread([js_debugger = js_debugger_, responder, params] {
      js_debugger->WhiteBoardDisable(responder, params);
    });
  } else {
    responder->SendError(CDPErrorCode::InternalError,
                         "WhiteBoard backend is unavailable");
  }
}

void LynxDevToolMediator::WhiteBoardSetSharedData(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [executor = element_executor_, responder, params] {
                      executor->WhiteBoardSetSharedData(responder, params);
                    });
  } else if (js_task_runner_ && js_debugger_) {
    RunOnJSThread([js_debugger = js_debugger_, responder, params] {
      js_debugger->WhiteBoardSetSharedData(responder, params);
    });
  } else {
    responder->SendError(CDPErrorCode::InternalError,
                         "WhiteBoard backend is unavailable");
  }
}

void LynxDevToolMediator::WhiteBoardGetSharedData(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [executor = element_executor_, responder, params] {
                      executor->WhiteBoardGetSharedData(responder, params);
                    });
  } else if (js_task_runner_ && js_debugger_) {
    RunOnJSThread([js_debugger = js_debugger_, responder, params] {
      js_debugger->WhiteBoardGetSharedData(responder, params);
    });
  } else {
    responder->SendError(CDPErrorCode::InternalError,
                         "WhiteBoard backend is unavailable");
  }
}

void LynxDevToolMediator::WhiteBoardRemoveSharedData(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [executor = element_executor_, responder, params] {
                      executor->WhiteBoardRemoveSharedData(responder, params);
                    });
  } else if (js_task_runner_ && js_debugger_) {
    RunOnJSThread([js_debugger = js_debugger_, responder, params] {
      js_debugger->WhiteBoardRemoveSharedData(responder, params);
    });
  } else {
    responder->SendError(CDPErrorCode::InternalError,
                         "WhiteBoard backend is unavailable");
  }
}

void LynxDevToolMediator::WhiteBoardClear(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (tasm_task_runner_ && element_executor_) {
    RunOnTaskRunner(tasm_task_runner_,
                    [executor = element_executor_, responder, params] {
                      executor->WhiteBoardClear(responder, params);
                    });
  } else if (js_task_runner_ && js_debugger_) {
    RunOnJSThread([js_debugger = js_debugger_, responder, params] {
      js_debugger->WhiteBoardClear(responder, params);
    });
  } else {
    responder->SendError(CDPErrorCode::InternalError,
                         "WhiteBoard backend is unavailable");
  }
}

void LynxDevToolMediator::SendCDPEvent(const Json::Value& msg) {
  SendCDPEventImpl(msg);
}

void LynxDevToolMediator::SendCDPEvent(const std::string& msg) {
  SendCDPEventImpl(msg);
}

template <typename MsgType>
void LynxDevToolMediator::SendCDPEventImpl(const MsgType& msg) {
  auto devtool = devtool_wp_.lock();
  CHECK_NULL_AND_LOG_RETURN(devtool, "devtool is null");
  devtool->GetMessageSender()->SendMessage("CDP", msg);

  // send cdp event message to the SDK-side listener
  std::lock_guard<std::mutex> lock(cdp_event_listener_mutex_);
  for (auto& listener : cdp_event_listener_map_) {
    listener.second->SendMessage("CDP", msg);
  }
}

void LynxDevToolMediator::AddCDPEventListener(
    const std::string& name, const std::shared_ptr<MessageSender>& listener) {
  std::lock_guard<std::mutex> lock(cdp_event_listener_mutex_);
  auto it = cdp_event_listener_map_.find(name);
  if (it != cdp_event_listener_map_.end()) {
    LOGI("CDPEventListener has exists:" << it->first);
  }
  cdp_event_listener_map_[name] = listener;
}

void LynxDevToolMediator::RemoveCDPEventListener(const std::string& name) {
  LOGI("RemoveCDPEventListener: " << name);
  std::lock_guard<std::mutex> lock(cdp_event_listener_mutex_);
  cdp_event_listener_map_.erase(name);
}

void LynxDevToolMediator::EmulateTouchFromMouseEvent(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (!ui_executor_ ||
      !RunOnUIThread([responder, params, executor = ui_executor_] {
        executor->EmulateTouchFromMouseEvent(responder, params);
      })) {
    responder->SendError(CDPErrorCode::InternalError,
                         "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::DispatchMouseEvent(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (!ui_executor_ ||
      !RunOnUIThread([responder, params, executor = ui_executor_] {
        executor->DispatchMouseEvent(responder, params);
      })) {
    responder->SendError(CDPErrorCode::InternalError,
                         "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::InsertText(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (!ui_executor_ ||
      !RunOnUIThread([responder, params, executor = ui_executor_] {
        executor->InsertText(responder, params);
      })) {
    responder->SendError(CDPErrorCode::InternalError,
                         "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::SynthesizeTapGesture(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (!ui_executor_ ||
      !RunOnUIThread([responder, params, executor = ui_executor_] {
        executor->SynthesizeTapGesture(responder, params);
      })) {
    responder->SendError(CDPErrorCode::InternalError,
                         "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::InspectorEnable(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!devtool_executor_ ||
      !RunOnDevToolThread([sender, message, executor = devtool_executor_] {
        executor->InspectorEnable(sender, message);
      })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::InspectorDetached(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!devtool_executor_ ||
      !RunOnDevToolThread([sender, message, executor = devtool_executor_] {
        executor->InspectorDetached(sender, message);
      })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::PerformanceEnable(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!ui_executor_ ||
      !RunOnUIThread([sender, message, executor = ui_executor_] {
        executor->PerformanceEnable(sender, message);
      })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::PerformanceDisable(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!ui_executor_ ||
      !RunOnUIThread([sender, message, executor = ui_executor_] {
        executor->PerformanceDisable(sender, message);
      })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::getAllTimingInfo(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!ui_executor_ ||
      !RunOnUIThread([sender, message, executor = ui_executor_] {
        executor->getAllTimingInfo(sender, message);
      })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::getAllPerformanceEntries(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!ui_executor_ ||
      !RunOnUIThread([sender, message, executor = ui_executor_] {
        executor->getAllPerformanceEntries(sender, message);
      })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::LogEnable(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (!devtool_executor_ ||
      !RunOnDevToolThread([responder, params, executor = devtool_executor_] {
        executor->LogEnable(responder, params);
      })) {
    responder->SendError(CDPErrorCode::InternalError,
                         "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::LogDisable(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (!devtool_executor_ ||
      !RunOnDevToolThread([responder, params, executor = devtool_executor_] {
        executor->LogDisable(responder, params);
      })) {
    responder->SendError(CDPErrorCode::InternalError,
                         "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::LogClear(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (!devtool_executor_ ||
      !RunOnDevToolThread([responder, params, executor = devtool_executor_] {
        executor->LogClear(responder, params);
      })) {
    responder->SendError(CDPErrorCode::InternalError,
                         "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::SendLogEntryAddedEvent(
    const lynx::runtime::js::ConsoleMessage& message) {
  RunOnDevToolThread([message, executor = devtool_executor_] {
    executor->SendLogEntryAddedEvent(message);
  });
}

void LynxDevToolMediator::AddNativeModuleRecord(const lepus::Value& record) {
  auto manager = native_module_record_manager_;
  if (manager == nullptr) {
    return;
  }
  manager->EnqueueRecordOnJSThread(record);
}

void LynxDevToolMediator::NativeModuleEnable(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!RunOnDevToolThread(
          [sender, message, manager = native_module_record_manager_] {
            if (manager != nullptr) {
              manager->Enable();
            }
            sender->SendOKResponse(message["id"].asInt64());
          })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::NativeModuleDisable(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!RunOnDevToolThread(
          [sender, message, manager = native_module_record_manager_] {
            if (manager != nullptr) {
              manager->Disable();
            }
            sender->SendOKResponse(message["id"].asInt64());
          })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::NativeModuleGetRecords(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!RunOnDevToolThread(
          [sender, message, manager = native_module_record_manager_] {
            if (manager != nullptr) {
              manager->GetRecords(sender, message["id"].asInt64());
            } else {
              sender->SendOKResponse(message["id"].asInt64());
            }
          })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

// Network protocol
void LynxDevToolMediator::NetworkEnable(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!devtool_executor_ ||
      !RunOnDevToolThread([sender, message, executor = devtool_executor_] {
        executor->NetworkEnable(sender, message);
      })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::NetworkDisable(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!devtool_executor_ ||
      !RunOnDevToolThread([sender, message, executor = devtool_executor_] {
        executor->NetworkDisable(sender, message);
      })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::NetworkGetResponseBody(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!devtool_executor_ ||
      !RunOnDevToolThread([sender, message, executor = devtool_executor_] {
        executor->NetworkGetResponseBody(sender, message);
      })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::NetworkGetRequestPostData(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!devtool_executor_ ||
      !RunOnDevToolThread([sender, message, executor = devtool_executor_] {
        executor->NetworkGetRequestPostData(sender, message);
      })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::LayerTreeEnable(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (!element_executor_ ||
      !RunOnTASMThread([responder, params, executor = element_executor_] {
        executor->LayerTreeEnable(responder, params);
      })) {
    responder->SendError(CDPErrorCode::InternalError,
                         "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::LayerTreeDisable(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (!element_executor_ ||
      !RunOnTASMThread([responder, params, executor = element_executor_] {
        executor->LayerTreeDisable(responder, params);
      })) {
    responder->SendError(CDPErrorCode::InternalError,
                         "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::AnimationEnable(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (!element_executor_ ||
      !RunOnTASMThread([responder, params, executor = element_executor_] {
        executor->AnimationEnable(responder, params);
      })) {
    responder->SendError(CDPErrorCode::InternalError,
                         "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::AnimationDisable(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (!element_executor_ ||
      !RunOnTASMThread([responder, params, executor = element_executor_] {
        executor->AnimationDisable(responder, params);
      })) {
    responder->SendError(CDPErrorCode::InternalError,
                         "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::AnimationGetCurrentTime(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (!element_executor_ ||
      !RunOnTASMThread([responder, params, executor = element_executor_] {
        executor->AnimationGetCurrentTime(responder, params);
      })) {
    responder->SendError(CDPErrorCode::InternalError,
                         "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::AnimationSeekAnimations(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (!element_executor_ ||
      !RunOnTASMThread([responder, params, executor = element_executor_] {
        executor->AnimationSeekAnimations(responder, params);
      })) {
    responder->SendError(CDPErrorCode::InternalError,
                         "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::AnimationSetPaused(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (!element_executor_ ||
      !RunOnTASMThread([responder, params, executor = element_executor_] {
        executor->AnimationSetPaused(responder, params);
      })) {
    responder->SendError(CDPErrorCode::InternalError,
                         "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::AnimationReleaseAnimations(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (!element_executor_ ||
      !RunOnTASMThread([responder, params, executor = element_executor_] {
        executor->AnimationReleaseAnimations(responder, params);
      })) {
    responder->SendError(CDPErrorCode::InternalError,
                         "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::SendLayerTreeDidChangeEvent() {
  RunOnTASMThread([executor = element_executor_] {
    executor->SendLayerTreeDidChangeEvent();
  });
}

void LynxDevToolMediator::CompositingReasons(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (!element_executor_ ||
      !RunOnTASMThread([responder, params, executor = element_executor_] {
        executor->CompositingReasons(responder, params);
      })) {
    responder->SendError(CDPErrorCode::InternalError,
                         "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::GetBoxModel(
    const InspectorBoxModelQuery& query,
    std::function<void(std::vector<double>)> callback) {
  if (!ui_task_runner_ || !ui_executor_) {
    RunCallbackOnTasmThread(tasm_task_runner_, std::move(callback), {});
    return;
  }
  RunOnTaskRunner(ui_task_runner_, [executor = ui_executor_, query,
                                    callback = std::move(callback),
                                    tasm_runner = tasm_task_runner_]() mutable {
    RunCallbackOnTasmThread(tasm_runner, std::move(callback),
                            executor->GetBoxModel(query));
  });
}

void LynxDevToolMediator::GetBoxModels(
    const std::vector<InspectorBoxModelQuery>& queries,
    std::function<void(std::vector<std::vector<double>>)> callback) {
  if (!ui_task_runner_ || !ui_executor_) {
    RunCallbackOnTasmThread(tasm_task_runner_, std::move(callback), {});
    return;
  }
  RunOnTaskRunner(ui_task_runner_, [executor = ui_executor_, queries,
                                    callback = std::move(callback),
                                    tasm_runner = tasm_task_runner_]() mutable {
    std::vector<std::vector<double>> box_models;
    box_models.reserve(queries.size());
    for (const auto& query : queries) {
      box_models.push_back(executor->GetBoxModel(query));
    }
    RunCallbackOnTasmThread(tasm_runner, std::move(callback),
                            std::move(box_models));
  });
}

SLNode* LynxDevToolMediator::GetLayoutObjectById(int32_t id) {
  if (!ui_task_runner_->RunsTasksOnCurrentThread()) {
    LOGE(
        "LynxDevToolMediator::GetLayoutObjectById must be called on the UI "
        "thread");
    return nullptr;
  }
  return ui_executor_->GetLayoutObjectById(id);
}

void LynxDevToolMediator::GetLayoutTree(
    int32_t id, std::function<void(std::string)> callback) {
  if (!ui_task_runner_ || !ui_executor_) {
    RunCallbackOnTasmThread(tasm_task_runner_, std::move(callback),
                            std::string());
    return;
  }
  RunOnTaskRunner(ui_task_runner_, [executor = ui_executor_, id,
                                    callback = std::move(callback),
                                    tasm_runner = tasm_task_runner_]() mutable {
    auto* layout_node = executor->GetLayoutObjectById(id);
    if (layout_node == nullptr) {
      RunCallbackOnTasmThread(tasm_runner, std::move(callback), std::string());
      return;
    }
    RunCallbackOnTasmThread(
        tasm_runner, std::move(callback),
        lynx::tasm::replay::ReplayController::GetLayoutTree(layout_node));
  });
}

void LynxDevToolMediator::SendLayoutTree() {
  RunOnTASMThread(
      [executor = element_executor_] { executor->SendLayoutTree(); });
}

void LynxDevToolMediator::FlushLayoutTreeForReplayEnd(
    std::function<void()> callback) {
  if (!tasm_task_runner_) {
    if (callback) {
      callback();
    }
    return;
  }
  RunOnTaskRunner(
      tasm_task_runner_,
      [executor = element_executor_, callback = std::move(callback)]() mutable {
        if (executor) {
          executor->FlushLayoutTreeForReplayEnd(std::move(callback));
          return;
        }
        if (callback) {
          callback();
        }
      });
}

void LynxDevToolMediator::LynxGetProperties(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!element_executor_ ||
      !RunOnTASMThread([sender, message, executor = element_executor_] {
        executor->LynxGetProperties(sender, message);
      })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::LynxGetData(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!element_executor_ ||
      !RunOnTASMThread([sender, message, executor = element_executor_] {
        executor->LynxGetData(sender, message);
      })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::LynxGetComponentId(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!element_executor_ ||
      !RunOnTASMThread([sender, message, executor = element_executor_] {
        executor->LynxGetComponentId(sender, message);
      })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::LynxSetTraceMode(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!devtool_executor_ ||
      !RunOnDevToolThread([sender, message, executor = devtool_executor_] {
        executor->LynxSetTraceMode(sender, message);
      })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

bool LynxDevToolMediator::LynxSetLogLevel(
    const std::shared_ptr<CDPResponder>& responder, int level) {
  return ui_executor_ &&
         RunOnUIThread([executor = ui_executor_, responder, level] {
           executor->LynxSetLogLevel(responder, level);
         });
}

void LynxDevToolMediator::LynxGetRectToWindow(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!ui_executor_ ||
      !RunOnUIThread([sender, message, executor = ui_executor_] {
        executor->LynxGetRectToWindow(sender, message);
      })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::LynxGetVersion(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!devtool_executor_ ||
      !RunOnDevToolThread([sender, message, executor = devtool_executor_] {
        executor->LynxGetVersion(sender, message);
      })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::LynxTransferData(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!ui_executor_ ||
      !RunOnUIThread([sender, message, executor = ui_executor_] {
        executor->LynxTransferData(sender, message);
      })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::LynxGetViewLocationOnScreen(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!ui_executor_ ||
      !RunOnUIThread([sender, message, executor = ui_executor_] {
        executor->LynxGetViewLocationOnScreen(sender, message);
      })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::LynxSendEventToVM(
    const std::shared_ptr<lynx::devtool::MessageSender>& sender,
    const Json::Value& message) {
  if (!ui_executor_ ||
      !RunOnUIThread([sender, message, executor = ui_executor_] {
        executor->LynxSendEventToVM(sender, message);
      })) {
    sender->SendErrorResponse(message["id"].asInt64(), -32603,
                              "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::TemplateGetTemplateData(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (!ui_executor_ ||
      !RunOnUIThread([responder, params, executor = ui_executor_] {
        executor->TemplateGetTemplateData(responder, params);
      })) {
    responder->SendError(CDPErrorCode::InternalError,
                         "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::TemplateGetTemplateJsInfo(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (!ui_executor_ ||
      !RunOnUIThread([responder, params, executor = ui_executor_] {
        executor->TemplateGetTemplateJsInfo(responder, params);
      })) {
    responder->SendError(CDPErrorCode::InternalError,
                         "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::TemplateGetTemplateApiInfo(
    const std::shared_ptr<CDPResponder>& responder, const Json::Value& params) {
  if (!element_executor_ ||
      !RunOnTASMThread([responder, params, executor = element_executor_] {
        executor->TemplateGetTemplateApiInfo(responder, params);
      })) {
    responder->SendError(CDPErrorCode::InternalError,
                         "CDP backend is unavailable");
  }
}

void LynxDevToolMediator::InitWhiteBoardInspector(
    const std::shared_ptr<tasm::WhiteBoardDelegate>& delegate,
    const std::shared_ptr<WhiteBoardInspectorDelegate>& inspector_delegate) {
  if (delegate == nullptr || inspector_delegate == nullptr) {
    return;
  }
  auto inspector = std::static_pointer_cast<WhiteBoardInspectorImpl>(
      delegate->GetInspector());
  if (inspector == nullptr) {
    inspector = std::make_shared<WhiteBoardInspectorImpl>();
    delegate->SetInspector(inspector);
  }
  inspector->InsertDelegate(inspector_delegate, view_id_);
  inspector_delegate->SetInspector(inspector);
}

}  // namespace devtool
}  // namespace lynx

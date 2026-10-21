# cdpc todo 实施与验证记录

日期：2026-10-21。分支：`feature/cdpc-v2`。本轮以 `1c53c43a2` 为起点，根据仓库根目录的 `cdpc_todo.md` 完成 P0、P1 的代码工作和用户确认的 P2-1、P2-2、P2-3。设备及 Explorer/Elements 验证按用户要求暂缓；P2-4 的移动端录制开关未列入本轮。

## 完成范围

| 项目 | 实施结果 |
|---|---|
| P0-1、P0-2 | Python/C++ 支持联合类型、null、动态 map；共享用例检查两端结论一致 |
| P0-3、P0-4 | 登记 4 项非对象根值；补齐缺少 items 的数组声明 |
| P0-5 | 契约 JSON 升为 v2，显式列出 unsupported；write 默认拒绝遗漏，提供临时允许选项；两端统一 kUnsupportedContract |
| P0-6、P0-7 | 内部 KeyError 保留堆栈；kUnanswered 定位到请求行 |
| P0-8、P0-9 | harness 即时输出违规并恢复旧 observer；recorder 复用序列化结果，限制队列，记录丢弃统计 |
| P0-10 | DevTool CI 加入 strict check 和 Python 测试；本地等价命令通过，尚未运行远端 CI |
| P1-1 | 新增 ForEachInvalidParamsVariant；Input、Page、DOM、CSS 合计 386 个独立变体均返回 -32602，无崩溃、无跳过清单 |
| P1-2 | capture 仅回放明确标记 safe 的示例，解析节点占位符、保存请求/响应并自动 verify；依赖缺失、错误解析、文件保护均有测试 |
| P1-3 | DOM、CSS、Page、Input、UITree、Animation、LayerTree 接入 harness，逐域提交；覆盖可用的事件发送路径 |
| P1-4 | 登记 DOM.compress、CSS.extraFields 和 Page 资源树 frame 字段偏差；真实执行器单测和构造 trace 验证通过，设备原始 trace 重验待做 |
| P1-5 | WhiteBoard notes 与禁用/缺失 delegate 的实际错误行为对齐 |
| P2-1 | responder 丢弃时记录 LOGE、通知 observer，并回复 InternalError，停止兜底成功 |
| P2-2 | 56 个 handler 在转换及业务操作前校验类型、原生数值范围、嵌套对象和数组；生产代码不依赖测试契约 JSON |
| P2-3 | 清单 93 处路径补充错误回复；继续补齐转发失败及异步压缩线程缺失等路径；截图、CSS 使用追踪更新和模板分片请求明确回复 |

## 本地验证

日志目录：`/tmp/cdpc-todo-20261021/`。

| 验证 | 结果 | 日志 |
|---|---|---|
| Python 全量 | 51 tests passed | `final-python.log` |
| DevTool Agent | 299 tests passed；1 个原有 disabled test | `final-agent-fixed.log` |
| Base DevTool | 59 tests passed | `final-base.log` |
| Contract checker/harness | 24 tests passed | `p1-checker-numbers.log` |
| Shared data | 24 tests passed | `final-shared.log` |
| 四个 C++ 测试目标构建 | 成功 | `final-build.log`、`final-fix-build.log` |
| check --strict | 0 errors、4 warnings，退出码 0 | `final-strict.log` |
| write --dry-run | 0 文件变化、0 unsupported | `final-generated.log` |
| C++ recorder → Python verify | 5 条记录，仅报告故意注入的 1 个 kDropped，退出码 1 符合预期 | `final-recorder-verify.log` |
| 无回复清单核对 | 93 处所属 handler 均有明确错误回复 | `noreply-final-audit.json` |

C++ 合计 406 个测试通过。后端不可用测试覆盖 86 个旧接口、31 个 responder 接口、8 个全局接口，以及平台 facade、文档根节点和 mediator 缺失路径。responder 用例在析构前检查已收到回复，避免把兜底错误误认为正常完成。

4 个 warning 分别为 Lynx.getVersion 返回字符串、Template.templateConfigInfo 返回字符串、Replay.end 接收字符串参数、Component.uselessUpdate 事件允许非对象根值。它们均有带原因的 violation 登记；unsupported 列表为空。

## 接入时发现的契约问题

- 已修复 LayerTree.compositingReasons 的字段拼写及 ID 元素类型；空树 layerPainted 事件提供完整的空矩形。
- 已修复 C++ checker 对 NaN/Infinity 的检查，与 Python 保持一致。
- Page.navigate 仍缺少 result.frameId；Page.frameNavigated 仍缺少完整 frame 字段和 params.type。这两项既有问题保留在专门测试的 `Expect()` 中，没有把字段缺失改成合法契约。

## 待设备验证

本轮没有连接设备，也没有启动 Explorer。之后可按实际 client/session 执行：

```bash
tools/cdpc capture DOM CSS Page --client <client-id> --session <session-id> -o /tmp/cdpc-device.trace.jsonl
tools/cdpc verify /tmp/cdpc-device.trace.jsonl
```

当前本机 agent-lynx 的成功输出可解析为结果值，但失败时可能只输出文本堆栈。capture 会保留部分 trace 并返回环境错误；只有结构化 JSON 错误才能保留原始 CDP code/message。真实失败响应采集仍需支持 JSON 错误输出的 CLI。

Explorer/Elements 录制及同一份设备 trace 的回归验证继续待测。本地编译、单测和离线 trace 检查不替代这些验证。

## 提交索引

以下为实现与测试提交，不含本记录的文档提交。

```text
85ca1ac12 [Feature][DevTool] Complete CDP contract checks and enable CI validation
f3c760455 [Fix][DevTool] Record DOM CSS and Page compatibility deviations
2a6a12744 [Docs][DevTool] Align WhiteBoard notes with protocol behavior
71df03309 [Fix][DevTool] Return InternalError for dropped CDP requests
8aa4aca0c [Feature][DevTool] Capture safe CDP examples through agent-lynx
0e8e227f4 [Fix][DevTool] Validate CDP parameters before native conversions
4de9bf272 [Test][DevTool] Exercise contract-driven invalid parameter variants
f0c9e0b95 [Fix][DevTool] Reply explicitly when CDP backends are unavailable
c14a4ff8c [Fix][DevTool] Reject non-finite contract numbers consistently
c677ab8c8 [Fix][DevTool] Match LayerTree response fields to the CDP contract
771c05a04 [Test][DevTool] Check DOM traffic with the contract harness
a95977c5f [Test][DevTool] Check CSS traffic with the contract harness
d9825a6e4 [Test][DevTool] Check Page traffic with the contract harness
1c6c283ad [Test][DevTool] Check Input traffic with the contract harness
48bbb4b55 [Test][DevTool] Check UITree traffic with the contract harness
bf672d9b6 [Test][DevTool] Check Animation traffic with the contract harness
1e79e7c97 [Test][DevTool] Check LayerTree traffic with the contract harness
e748b1e50 [Test][DevTool] Cover missing dependencies and updated CDP responses
```

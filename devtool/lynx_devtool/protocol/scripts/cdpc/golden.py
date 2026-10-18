# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Golden request matrix, session scrubbing, and the replay unittest.

Sessions imported from a real DevTool frontend are scrubbed before they are
stored. The rules, applied by `scrub_value`:

- Keep CDP envelope fields that are not user content: `method`, numeric `id`,
  `sessionId`, and `error.code` / `error.message` (the frontend compares those).
- Drop messages whose method is outside the domain. Responses are kept when
  their id matches a kept request in the same session.
- Replace URL-like strings (`://`, `www.`, `lynx://`) with
  `https://placeholder.invalid/scrubbed`.
- Replace e-mail addresses and absolute filesystem paths with `<string>`.
- Under user-content objects (`globalProps`, `pageData`, `props`, `data`,
  `attributes`, `styles`, `html`, `text`, `content`, `title`, `url`, `src`,
  `href`, `uri`, and any object nested inside those) replace leaves with a
  placeholder of the same JSON type: strings become `<string>`, integers `0`,
  non-integers `0.0`, booleans `false`, null stays null. Keys and array
  lengths stay, so a later compare still sees the shape.
- Replace any other string longer than 80 characters. Those are page text,
  scripts, or tokens, not protocol enums.
- Rename a key that is itself a URL or e-mail to `keyN` so the key cannot
  carry user content either.

`check` rejects a stored session that still contains a URL host other than
`placeholder.invalid` or an e-mail address.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

from cdpc import codegen, contract
from cdpc.repo import CdpcError, EnvError, Paths, license_header, relative_path


URL_RE = re.compile(r"(?i)([a-z][a-z0-9+.-]*://|www\.)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PATH_RE = re.compile(r"^(?:/[^\s]+|[A-Za-z]:\\[^\s]+)$")
URL_PLACEHOLDER = "https://placeholder.invalid/scrubbed"
STRING_PLACEHOLDER = "<string>"
USER_KEYS = frozenset(
    {
        "globalProps",
        "pageData",
        "props",
        "data",
        "attributes",
        "styles",
        "html",
        "text",
        "content",
        "title",
        "url",
        "src",
        "href",
        "uri",
    }
)
ENVELOPE_KEYS = frozenset({"method", "id", "sessionId", "error", "result", "params"})
LONG_STRING = 80
ENVS = (
    {"tasm": "null", "runner": "tasm"},
    {"tasm": "null", "runner": "none"},
)
# The unit-test TemplateAssembler does not notify the devtool observer, so the
# harness fires this event itself. Other domains get no event hook until one
# is added here.
EVENT_HOOKS = {
    "GlobalProps.changed": (
        "element_executor_->SetGlobalPropsEnabled(true);\n"
        "    element_executor_->GlobalPropsChanged();"
    ),
}


class GoldenError(CdpcError):
    pass


def canonical(value: Any) -> str:
    """Sorted-key compact JSON. Integers stay integers; 1 and 1.0 differ."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def scrub_value(value: Any, *, user: bool = False, key: str = "") -> Any:
    """Return `value` with user content replaced by a same-typed placeholder."""

    if isinstance(value, dict):
        if key == "error":
            return {
                "code": value.get("code"),
                "message": value.get("message"),
                **{
                    child: scrub_value(item, user=False, key=child)
                    for child, item in value.items()
                    if child not in ("code", "message")
                },
            }
        scrubbed = {}
        renamed = 0
        for child, item in value.items():
            name = child
            if _sensitive_string(child):
                renamed += 1
                name = f"key{renamed}"
            child_user = user or child in USER_KEYS or name in USER_KEYS
            if child == "method" and isinstance(item, str):
                scrubbed[name] = item
            elif child == "id" and isinstance(item, (int, float)) and not isinstance(item, bool):
                scrubbed[name] = item
            elif child == "sessionId" and isinstance(item, str):
                scrubbed[name] = STRING_PLACEHOLDER if _sensitive_string(item) or user else item
            else:
                scrubbed[name] = scrub_value(item, user=child_user, key=child)
        return scrubbed
    if isinstance(value, list):
        return [scrub_value(item, user=user) for item in value]
    if isinstance(value, str):
        return _scrub_string(value, user=user)
    if isinstance(value, bool) or value is None:
        return False if user and isinstance(value, bool) else value
    if isinstance(value, int) and not isinstance(value, bool):
        return 0 if user else value
    if isinstance(value, float):
        return 0.0 if user else value
    return value


def _scrub_string(value: str, *, user: bool) -> str:
    if URL_RE.search(value):
        return URL_PLACEHOLDER
    if EMAIL_RE.search(value) or PATH_RE.match(value):
        return STRING_PLACEHOLDER
    if user or len(value) > LONG_STRING:
        return STRING_PLACEHOLDER
    return value


def _sensitive_string(value: str) -> bool:
    return bool(URL_RE.search(value) or EMAIL_RE.search(value))


def scrub_session(lines: list[str], domain: str) -> list[dict]:
    """Filter `lines` to `domain` and scrub every kept message."""

    messages = []
    for index, line in enumerate(lines, 1):
        text = line.strip()
        if not text:
            continue
        try:
            message = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GoldenError(f"session line {index} is not JSON: {exc}") from exc
        if not isinstance(message, dict):
            raise GoldenError(f"session line {index} must be a JSON object")
        messages.append(message)
    kept_ids = set()
    kept = []
    for message in messages:
        method = message.get("method")
        if isinstance(method, str) and method.startswith(domain + "."):
            if "id" in message:
                kept_ids.add(message["id"])
            kept.append(scrub_value(message))
        elif "id" in message and "method" not in message and message["id"] in kept_ids:
            kept.append(scrub_value(message))
    return kept


def session_leaks(message: Any) -> list[str]:
    """Protocol-looking user content that should not survive scrubbing."""

    leaks = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if _sensitive_string(str(key)):
                    leaks.append(path + "." + key)
                walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")
        elif isinstance(value, str):
            if URL_RE.search(value) and URL_PLACEHOLDER not in value:
                leaks.append(path)
            elif EMAIL_RE.search(value):
                leaks.append(path)

    walk(message, "$")
    return leaks


def import_session(paths: Paths, domain: str, source: Path) -> tuple[Path, int]:
    """Scrub `source` and write it under testdata/golden/sessions. Returns path and count."""

    if not source.exists():
        raise GoldenError(f"session file does not exist: {source}")
    messages = scrub_session(source.read_text(encoding="utf-8").splitlines(), domain)
    for message in messages:
        leaks = session_leaks(message)
        if leaks:
            raise GoldenError(f"scrubbing left user content at {', '.join(leaks)}")
    directory = paths.protocol_dir / "testdata" / "golden" / "sessions"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{domain}.{source.stem}.cdp.jsonl"
    body = "".join(json.dumps(message, sort_keys=True, separators=(",", ":")) + "\n" for message in messages)
    target.write_text(body, encoding="utf-8")
    return target, len(messages)


def build_matrix(contracts: contract.ContractSet, domain: str) -> list[dict]:
    """One case per method for the kinds the harness can produce without a page."""

    cases = []
    entries = [entry for entry in contract.typed_entries(contracts, domain) if entry.kind == "method"]
    for entry in entries:
        doc = contracts.methods.get((domain, entry.name))
        examples = doc.examples if doc is not None else []
        requests = [example["request"] for example in examples if isinstance(example.get("request"), dict)]
        if not requests:
            requests = [{"id": 1, "method": entry.key, "params": _sample_params(entry)}]
        for request in requests:
            cases.append(_case(entry.name, "normal", "example", request))
        fields = list((entry.parameters.properties or {}).items())
        for name, prop in fields:
            if not prop.optional:
                params = _sample_params(entry)
                params.pop(name, None)
                cases.append(_case(entry.name, "missing", name, _request(entry.key, params)))
            params = _sample_params(entry)
            params[name] = _wrong_type(prop.schema)
            cases.append(_case(entry.name, "wrong-type", name, _request(entry.key, params)))
        extra = _sample_params(entry)
        extra["cdpcExtra"] = 1
        cases.append(_case(entry.name, "extra", "field", _request(entry.key, extra)))
        cases.append(_case(entry.name, "params", "null", {"id": 1, "method": entry.key, "params": None}))
        cases.append(_case(entry.name, "params", "array", {"id": 1, "method": entry.key, "params": []}))
    cases.append(
        _case("unknown", "method", "nope", {"id": 1, "method": f"{domain}.nope", "params": {}})
    )
    expanded = []
    for case in cases:
        for env in ENVS:
            label = "runner-none" if env["runner"] == "none" else "tasm-null"
            expanded.append({**case, "case": f"{case['case']}/{label}", "env": dict(env)})
    for event in contract.typed_entries(contracts, domain):
        if event.kind != "event" or event.key not in EVENT_HOOKS:
            continue
        expanded.append(
            {
                "case": f"{event.name}/enabled/tasm-null",
                "pre": [],
                "request": {"id": 1, "method": f"{domain}.enable", "params": {}},
                "env": {"tasm": "null", "runner": "tasm", "event": event.key},
            }
        )
    return expanded


def _case(method: str, kind: str, detail: str, request: dict) -> dict:
    return {"case": f"{method}/{kind}/{detail}", "pre": [], "request": request}


def _request(method: str, params: Any) -> dict:
    return {"id": 1, "method": method, "params": params}


def _sample_params(entry: contract.TypedEntry) -> dict:
    params = {}
    for name, prop in (entry.parameters.properties or {}).items():
        if prop.optional:
            continue
        params[name] = _sample_value(prop.schema)
    return params


def _sample_value(schema: contract.Schema) -> Any:
    if schema.enum:
        return schema.enum[0]
    kinds = schema.kinds
    if kinds == {"string"}:
        return "sample"
    if kinds == {"integer"}:
        return 1
    if kinds == {"number"}:
        return 1.5
    if kinds == {"boolean"}:
        return True
    if kinds == {"array"}:
        return []
    if "object" in kinds or not kinds:
        return {}
    return {}


def _wrong_type(schema: contract.Schema) -> Any:
    kinds = schema.kinds
    if "object" in kinds:
        return []
    if "array" in kinds:
        return {}
    if "string" in kinds:
        return 0
    if "boolean" in kinds:
        return "x"
    return []


def render_requests_yaml(cases: list[dict]) -> str:
    lines = [
        *license_header("#"),
        "#",
        "# Generated by `tools/cdpc stubs`. Request matrix for the golden harness.",
        "",
    ]
    for case in cases:
        lines.append(f"- case: {json.dumps(case['case'])}")
        lines.append(f"  request: {json.dumps(case['request'], sort_keys=True)}")
        lines.append(f"  env: {json.dumps(case.get('env', {}), sort_keys=True)}")
        if case.get("pre"):
            lines.append(f"  pre: {json.dumps(case['pre'], sort_keys=True)}")
    lines.append("")
    return "\n".join(lines)


def load_jsonl(path: Path) -> list[dict]:
    cases = []
    if not path.exists():
        raise GoldenError(f"missing golden file {path}")
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GoldenError(f"{path}:{number}: invalid JSON: {exc}") from exc
        if not isinstance(item, dict) or "case" not in item or "request" not in item:
            raise GoldenError(f"{path}:{number}: a golden case needs case and request")
        cases.append(item)
    return cases


def check_golden(paths: Paths, contracts: contract.ContractSet) -> tuple[list[str], list[str]]:
    """Static golden checks. Returns (errors, warnings)."""

    errors: list[str] = []
    warnings: list[str] = []
    version = _lynx_version(paths)
    for domain, spec in sorted(contracts.domain_golden.items()):
        if not spec.file:
            continue
        path = paths.source_root / spec.file
        try:
            cases = load_jsonl(path)
        except GoldenError as exc:
            errors.append(str(exc))
            continue
        ids = [case["case"] for case in cases]
        if ids != sorted(ids):
            errors.append(f"{spec.file}: cases must be sorted by id")
        matrix = build_matrix(contracts, domain)
        matrix_ids = {case["case"] for case in matrix}
        missing = sorted(matrix_ids - set(ids))
        if missing:
            errors.append(
                f"{spec.file}: matrix cases missing from the golden: {', '.join(missing[:8])}"
            )
        known_methods = {
            entry.key
            for entry in contract.typed_entries(contracts, domain)
            if entry.kind == "method"
        }
        known_events = {
            entry.key
            for entry in contract.typed_entries(contracts, domain)
            if entry.kind == "event"
        }
        for case in cases:
            method = case.get("request", {}).get("method")
            if isinstance(method, str) and method in known_methods or (
                isinstance(method, str) and method.startswith(domain + ".nope")
            ):
                pass
            elif isinstance(method, str) and method.startswith(domain + "."):
                errors.append(f"{spec.file}: {case['case']} uses unknown method {method}")
            for item in case.get("out") or []:
                raw = item.get("raw") if isinstance(item, dict) else None
                if not isinstance(raw, str):
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    errors.append(f"{spec.file}: {case['case']} has an out line that is not JSON")
                    continue
                event = payload.get("method") if isinstance(payload, dict) else None
                if isinstance(event, str) and event not in known_events:
                    errors.append(f"{spec.file}: {case['case']} emits unknown event {event}")
        for diff in spec.allowed_diffs:
            if version and diff.expires and _version_ge(version, diff.expires):
                errors.append(
                    f"{domain}: allowedDiff {diff.case} {diff.path} expired at {diff.expires} "
                    f"(current {version})"
                )
        requests = path.with_name(f"{domain}.requests.yaml")
        if not requests.exists():
            warnings.append(f"{domain}: no {requests.name}; stubs --apply writes the request matrix")
    session_dir = paths.protocol_dir / "testdata" / "golden" / "sessions"
    if session_dir.exists():
        for session in sorted(session_dir.glob("*.cdp.jsonl")):
            for number, line in enumerate(session.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    errors.append(f"{relative_path(paths, session)}:{number}: {exc}")
                    continue
                leaks = session_leaks(payload)
                if leaks:
                    errors.append(
                        f"{relative_path(paths, session)}:{number}: stored session was not scrubbed "
                        f"({', '.join(leaks)})"
                    )
    return errors, warnings


def _lynx_version(paths: Paths) -> Optional[str]:
    from cdpc import scan

    config = getattr(paths, "lynx_config_source", None)
    if config is None:
        return None
    try:
        return scan.scan_current_lynx_version(config)
    except CdpcError:
        return None


def _version_ge(current: str, expires: str) -> bool:
    def parse(value: str) -> tuple[int, int]:
        major, minor = value.split(".", 1)
        return int(major), int(minor)

    return parse(current) >= parse(expires)


def diff_allowed(case_id: str, path: str, before: str, after: str, spec: contract.GoldenSpec) -> bool:
    for item in spec.allowed_diffs:
        if item.path == path and fnmatch.fnmatch(case_id, item.case):
            if item.from_text == before and item.to_text == after:
                return True
    return False


def run_golden_bin(executable: Path) -> str:
    """Replay every golden gtest. Returns combined output; raises EnvError if it is missing."""

    if not executable.exists():
        raise EnvError(
            f"golden executable does not exist: {executable}. "
            "Build devtool_agent_unittest_exec first."
        )
    result = subprocess.run(
        [str(executable), "--gtest_filter=*Golden*"],
        capture_output=True,
        text=True,
        check=False,
    )
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        raise GoldenError(
            f"golden replay failed ({executable}, exit {result.returncode}):\n{output[-4000:]}"
        )
    return output


def render_unittest(
    domain: str,
    namespace: str,
    class_name: str,
    golden_file: str,
    volatile: list[str],
) -> str:
    """C++ replay test. Recording (`CDPC_GOLDEN_RECORD=1`) rewrites the jsonl."""

    volatile_lines = "\n".join(f'    "{path}",' for path in volatile)
    hooks = []
    for name, body in EVENT_HOOKS.items():
        if name.startswith(domain + "."):
            hooks.append(
                f'  if (name == "{name}") {{\n    {body}\n    return;\n  }}'
            )
    hook = "\n".join(hooks) if hooks else '  (void)name;'
    lines = [
        *license_header("//"),
        "//",
        "// Generated by `tools/cdpc stubs`. Replays the golden file for this domain.",
        "// CDPC_GOLDEN overrides the jsonl path. CDPC_GOLDEN_RECORD=1 rewrites it.",
        "",
        "#define private public",
        "#define protected public",
        "",
        "#include <algorithm>",
        "#include <cstdlib>",
        "#include <fstream>",
        "#include <future>",
        "#include <map>",
        "#include <memory>",
        "#include <sstream>",
        "#include <string>",
        "#include <utility>",
        "#include <vector>",
        "",
        "#include \"base/include/fml/thread.h\"",
        "#include \"core/base/threading/task_runner_manufactor.h\"",
        "#include \"devtool/base_devtool/native/public/cdp_domain_agent_base.h\"",
        "#include \"devtool/base_devtool/native/public/cdp_responder.h\"",
        "#include \"devtool/base_devtool/native/public/message_sender.h\"",
        f'#include "devtool/lynx_devtool/agent/domain_agent/inspector_{namespace}_agent.h"',
        "#include \"devtool/lynx_devtool/agent/inspector_tasm_executor.h\"",
        "#include \"devtool/lynx_devtool/agent/lynx_devtool_mediator.h\"",
        "#include \"devtool/testing/mock/lynx_devtool_ng_mock.h\"",
        "#include \"third_party/googletest/googletest/include/gtest/gtest.h\"",
        "#include \"third_party/jsoncpp/include/json/json.h\"",
        "",
        "namespace lynx {",
        "namespace devtool {",
        "namespace testing {",
        "",
        "namespace {",
        "",
        f"constexpr char kGoldenFile[] = \"{golden_file}\";",
        "constexpr const char* kVolatile[] = {",
        volatile_lines,
        "};",
        "",
        "class RecordingSender : public MessageSender {",
        " public:",
        "  void SendMessage(const std::string&, const Json::Value& message) override {",
        "    Json::StreamWriterBuilder builder;",
        '    builder["indentation"] = "";',
        "    messages.push_back(Json::writeString(builder, message));",
        "  }",
        "  void SendMessage(const std::string&, const std::string& message) override {",
        "    messages.push_back(message);",
        "  }",
        "  std::vector<std::string> messages;",
        "};",
        "",
        "std::string FindGolden() {",
        "  if (const char* override_path = std::getenv(\"CDPC_GOLDEN\")) {",
        "    return override_path;",
        "  }",
        "  std::string dir = \".\";",
        "  for (int hop = 0; hop < 8; ++hop) {",
        "    const std::string candidate = dir + \"/\" + kGoldenFile;",
        "    std::ifstream input(candidate);",
        "    if (input.good()) {",
        "      return candidate;",
        "    }",
        "    dir += \"/..\";",
        "  }",
        "  return kGoldenFile;",
        "}",
        "",
        "std::string Escape(const std::string& text) {",
        "  std::string out;",
        "  for (char item : text) {",
        "    switch (item) {",
        "      case '\"':",
        "        out += \"\\\\\\\"\";",
        "        break;",
        "      case '\\\\':",
        "        out += \"\\\\\\\\\";",
        "        break;",
        "      default:",
        "        out += item;",
        "        break;",
        "    }",
        "  }",
        "  return out;",
        "}",
        "",
        "std::string Canonical(const Json::Value& value) {",
        "  if (value.isObject()) {",
        "    auto names = value.getMemberNames();",
        "    std::sort(names.begin(), names.end());",
        "    std::string out = \"{\";",
        "    for (size_t index = 0; index < names.size(); ++index) {",
        "      if (index != 0) out += \",\";",
        "      out += \"\\\"\" + Escape(names[index]) + \"\\\":\" + Canonical(value[names[index]]);",
        "    }",
        "    return out + \"}\";",
        "  }",
        "  if (value.isArray()) {",
        "    std::string out = \"[\";",
        "    for (Json::ArrayIndex index = 0; index < value.size(); ++index) {",
        "      if (index != 0) out += \",\";",
        "      out += Canonical(value[index]);",
        "    }",
        "    return out + \"]\";",
        "  }",
        "  if (value.isInt64() || value.isUInt64()) {",
        "    return std::to_string(value.asInt64());",
        "  }",
        "  if (value.isDouble()) {",
        "    Json::StreamWriterBuilder builder;",
        '    builder["indentation"] = "";',
        "    return Json::writeString(builder, value);",
        "  }",
        "  Json::StreamWriterBuilder builder;",
        '  builder["indentation"] = "";',
        "  return Json::writeString(builder, value);",
        "}",
        "",
        "Json::Value* Walk(Json::Value* value, const std::string& path) {",
        "  Json::Value* cursor = value;",
        "  std::stringstream stream(path);",
        "  std::string part;",
        "  while (std::getline(stream, part, '.')) {",
        "    if (cursor == nullptr || !cursor->isObject() || !cursor->isMember(part)) {",
        "      return nullptr;",
        "    }",
        "    cursor = &(*cursor)[part];",
        "  }",
        "  return cursor;",
        "}",
        "",
        "void MaskVolatile(Json::Value* value, std::map<std::string, int64_t>* seen) {",
        "  for (const char* path : kVolatile) {",
        "    Json::Value* field = Walk(value, path);",
        "    if (field == nullptr || !field->isNumeric()) {",
        "      continue;",
        "    }",
        "    const int64_t number = field->asInt64();",
        "    auto previous = seen->find(path);",
        "    if (previous != seen->end()) {",
        "      EXPECT_GE(number, previous->second) << path;",
        "    }",
        "    (*seen)[path] = number;",
        "    *field = \"<monotonic>\";",
        "  }",
        "}",
        "",
        "std::string Normalize(const std::string& raw, std::map<std::string, int64_t>* seen) {",
        "  Json::Value value;",
        "  Json::Reader reader;",
        "  if (!reader.parse(raw, value, false)) {",
        "    return raw;",
        "  }",
        "  MaskVolatile(&value, seen);",
        "  return Canonical(value);",
        "}",
        "",
        "}  // namespace",
        "",
        f"class {class_name}GoldenTest : public ::testing::Test {{",
        " protected:",
        "  void SetUp() override {",
        "    lynx::base::UIThread::Init();",
        "    devtool_mediator_ = std::make_shared<LynxDevToolMediator>();",
        "    devtools_ng_ = std::make_shared<lynx::testing::LynxDevToolNGMock>();",
        "    sender_ = std::make_shared<RecordingSender>();",
        "    devtools_ng_->message_sender_ = sender_;",
        "    devtool_mediator_->devtool_wp_ = devtools_ng_;",
        "    element_executor_ =",
        "        std::make_shared<InspectorTasmExecutor>(devtool_mediator_, nullptr, 1);",
        "    devtool_mediator_->element_executor_ = element_executor_;",
        f"    backend_ = std::make_unique<{class_name}>(devtool_mediator_);",
        f"    agent_ = protocol::{namespace}::CreateAgent(",
        f"        std::unique_ptr<protocol::{namespace}::Backend>(backend_.release()));",
        "  }",
        "",
        "  void ApplyEnv(const Json::Value& env) {",
        "    tasm_thread_.reset();",
        "    devtool_mediator_->tasm_task_runner_ = nullptr;",
        "    const bool no_runner =",
        "        env.isMember(\"runner\") && env[\"runner\"].isString() &&",
        "        env[\"runner\"].asString() == \"none\";",
        "    if (no_runner) {",
        "      return;",
        "    }",
        "    tasm_thread_ = std::make_unique<fml::Thread>(\"golden-tasm\");",
        "    devtool_mediator_->tasm_task_runner_ = tasm_thread_->GetTaskRunner();",
        "    if (!env.isMember(\"tasm\") || !env[\"tasm\"].isString() ||",
        "        env[\"tasm\"].asString() == \"null\") {",
        "      element_executor_->tasm_ = nullptr;",
        "    }",
        "  }",
        "",
        "  void Flush() {",
        "    if (!devtool_mediator_->tasm_task_runner_) {",
        "      return;",
        "    }",
        "    std::promise<void> promise;",
        "    auto future = promise.get_future();",
        "    devtool_mediator_->RunOnTASMThread([&promise] { promise.set_value(); }, true);",
        "    future.wait();",
        "  }",
        "",
        "  void Call(const Json::Value& request) {",
        "    int64_t id = 0;",
        "    if (request.isMember(\"id\") && request[\"id\"].isNumeric()) {",
        "      id = request[\"id\"].asInt64();",
        "    }",
        "    auto responder = std::make_shared<CDPResponder>(sender_, id);",
        "    agent_->CallMethod(responder, request);",
        "    Flush();",
        "  }",
        "",
        "  void FireEvent(const std::string& name) {",
        hook,
        "  }",
        "",
        "  std::shared_ptr<LynxDevToolMediator> devtool_mediator_;",
        "  std::shared_ptr<lynx::testing::LynxDevToolNGMock> devtools_ng_;",
        "  std::shared_ptr<RecordingSender> sender_;",
        "  std::shared_ptr<InspectorTasmExecutor> element_executor_;",
        f"  std::unique_ptr<{class_name}> backend_;",
        "  std::unique_ptr<CDPDomainAgentBase> agent_;",
        "  std::unique_ptr<fml::Thread> tasm_thread_;",
        "};",
        "",
        f"TEST_F({class_name}GoldenTest, Replay) {{",
        "  const std::string path = FindGolden();",
        "  std::ifstream input(path);",
        "  ASSERT_TRUE(input.good()) << path;",
        "  std::vector<Json::Value> cases;",
        "  std::string line;",
        "  while (std::getline(input, line)) {",
        "    if (line.empty()) continue;",
        "    Json::Value item;",
        "    Json::Reader reader;",
        "    ASSERT_TRUE(reader.parse(line, item, false)) << line;",
        "    cases.push_back(item);",
        "  }",
        "  std::sort(cases.begin(), cases.end(),",
        "            [](const Json::Value& left, const Json::Value& right) {",
        "              return left[\"case\"].asString() < right[\"case\"].asString();",
        "            });",
        "  const bool record = std::getenv(\"CDPC_GOLDEN_RECORD\") != nullptr;",
        "  std::vector<std::string> recorded;",
        "  for (const Json::Value& item : cases) {",
        "    SetUp();",
        "    ApplyEnv(item[\"env\"]);",
        "    for (const Json::Value& pre : item.get(\"pre\", Json::Value(Json::arrayValue))) {",
        "      Call(pre);",
        "    }",
        "    sender_->messages.clear();",
        "    Call(item[\"request\"]);",
        "    if (item[\"env\"].isMember(\"event\")) {",
        "      FireEvent(item[\"env\"][\"event\"].asString());",
        "    }",
        "    if (record) {",
        "      Json::Value copy = item;",
        "      copy[\"out\"] = Json::Value(Json::arrayValue);",
        "      for (const std::string& raw : sender_->messages) {",
        "        Json::Value parsed;",
        "        Json::Reader raw_reader;",
        "        const bool event =",
        "            raw_reader.parse(raw, parsed, false) && parsed.isObject() &&",
        "            parsed.isMember(\"method\") && parsed[\"method\"].isString();",
        "        Json::Value entry(Json::objectValue);",
        "        entry[\"kind\"] = event ? \"event\" : \"response\";",
        "        entry[\"raw\"] = raw;",
        "        copy[\"out\"].append(entry);",
        "      }",
        "      Json::StreamWriterBuilder builder;",
        '      builder["indentation"] = "";',
        "      recorded.push_back(Json::writeString(builder, copy));",
        "      continue;",
        "    }",
        "    if (!item.isMember(\"out\") || !item[\"out\"].isArray()) {",
        "      ADD_FAILURE() << item[\"case\"].asString()",
        "                    << \" has no out; record with CDPC_GOLDEN_RECORD=1\";",
        "      continue;",
        "    }",
        "    const Json::Value& expected = item[\"out\"];",
        "    ASSERT_EQ(sender_->messages.size(), expected.size()) << item[\"case\"].asString();",
        "    std::map<std::string, int64_t> actual_seen;",
        "    std::map<std::string, int64_t> expected_seen;",
        "    for (Json::ArrayIndex index = 0; index < expected.size(); ++index) {",
        "      const std::string got = Normalize(sender_->messages[index], &actual_seen);",
        "      const std::string want = Normalize(expected[index][\"raw\"].asString(), &expected_seen);",
        "      EXPECT_EQ(got, want) << item[\"case\"].asString();",
        "    }",
        "  }",
        "  if (record) {",
        "    std::ofstream output(path, std::ios::trunc);",
        "    for (const std::string& line_out : recorded) {",
        "      output << line_out << \"\\n\";",
        "    }",
        "  }",
        "}",
        "",
        "}  // namespace testing",
        "}  // namespace devtool",
        "}  // namespace lynx",
        "",
    ]
    return "\n".join(lines)


def namespace_of(domain: str) -> str:
    return codegen.snake_case(domain)

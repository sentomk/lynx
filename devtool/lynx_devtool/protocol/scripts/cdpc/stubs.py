# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""`cdpc stubs <Domain> [--apply]`: Backend overrides for a typed domain.

Without --apply, prints the override declarations. With --apply, adds the
missing ones to the only class implementing `protocol::<namespace>::Backend`:
declarations after its last Backend override, and definitions before the
closing namespaces of the `.cc` next to the header. Each new definition
replies MethodNotFound, so the build passes and an unfilled method fails
loudly instead of falling back to an empty success. Existing code is never
changed; overrides of methods the Backend no longer has are only reported.

When no class implements the Backend yet, --apply first scaffolds
`agent/domain_agent/inspector_<domain>_agent.{h,cc}`, starting with the Lynx
license header, and then adds the overrides to it.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from cdpc import codegen, contract, golden, inventory, scan, style
from cdpc.codegen import BackendMethod
from cdpc.repo import CdpcError, Paths, license_header, relative_path


class StubError(CdpcError):
    pass


def declarations(contracts: contract.ContractSet, domain: str) -> str:
    """The override declarations a Backend implementation needs."""

    _, methods = codegen.backend_methods(contracts, domain)
    lines = [f"  // {domain} Backend overrides."]
    lines += [f"  void {method.method}({method.args}) override;" for method in methods]
    return "\n".join(lines) + "\n"


def apply_to_domain(paths: Paths, contracts: contract.ContractSet, domain: str) -> str:
    """Advance one migration step, or add missing Backend overrides.

    A. old agent, no Backend: scaffold, adapter, registration, golden harness.
    B. Backend and adapter, migrated set changed: rewrite the adapter.
    C. migrated covers every method: delete the adapter and the domain legacy block.
    D. otherwise: add missing overrides, the previous behavior.
    """

    namespace, methods = codegen.backend_methods(contracts, domain)
    qualified = f"protocol::{namespace}"
    roots = [paths.source_root / "devtool"]
    if not roots[0].exists():
        roots = [paths.source_root]
    found = scan.find_backend_classes(roots, [qualified])[qualified]
    state = migration_state(paths, contracts, domain, found)
    if state == "A":
        return begin_migration(paths, contracts, domain, namespace, methods)
    if state == "C":
        return finish_migration(paths, contracts, domain, namespace, methods, found)
    report = ""
    if state == "B":
        report = refresh_adapter(paths, contracts, domain, namespace, methods)
    created: list[Path] = []
    if not found:
        header, class_name = scaffold(paths, domain, namespace)
        created = [header, header.with_suffix(".cc")]
    else:
        header, class_name = scan.find_backend_class(roots, qualified)
    result = apply(header, class_name, methods, getattr(paths, "clang_format", None))
    display = lambda path: relative_path(paths, path)
    created_report = "".join(
        style.item("+", f"created {display(path)}", "green") + "\n" for path in created
    )
    if created:
        created_report += style.wrap(
            f"Register it with RegisterAgent(\"{domain}\", {qualified}::CreateAgent("
            f"std::make_unique<{class_name}>(devtool_mediator_))) and add both files "
            "to agent/BUILD.gn.",
        ) + "\n"
    return report + created_report + format_result(result, display)


def migration_state(
    paths: Paths,
    contracts: contract.ContractSet,
    domain: str,
    backends: list[tuple[Path, str]],
) -> str:
    """A, B, C, or D. See the module docstring of apply_to_domain."""

    legacy = contracts.domain_legacy.get(domain)
    adapter = adapter_header(paths, codegen.snake_case(domain))
    if backends and legacy is not None and inventory.fully_migrated(contracts, domain):
        return "C"
    if backends and adapter.exists():
        return "B"
    if not backends and legacy_agent_source(paths, contracts, domain) is not None:
        return "A"
    return "D"


def scaffold(paths: Paths, domain: str, namespace: str) -> tuple[Path, str]:
    """Create an empty Backend implementation of `domain`; returns header and class."""

    class_name = f"Inspector{domain}Agent"
    header = paths.domain_agent_dir / f"inspector_{namespace}_agent.h"
    source = header.with_suffix(".cc")
    for path in (header, source):
        if path.exists():
            raise StubError(
                f"{relative_path(paths, path)} exists but does not implement "
                f"protocol::{namespace}::Backend; derive its class from it first"
            )
    header_include = header.relative_to(paths.source_root).as_posix()
    header.write_text(header_text(header_include, class_name, namespace), encoding="utf-8")
    source.write_text(source_text(header_include, class_name), encoding="utf-8")
    return header, class_name


def header_text(include: str, class_name: str, namespace: str) -> str:
    guard = "".join(c if c.isalnum() else "_" for c in include).upper() + "_"
    lines = [
        *license_header("//"),
        "",
        f"#ifndef {guard}",
        f"#define {guard}",
        "",
        "#include <memory>",
        "",
        '#include "devtool/lynx_devtool/protocol/cdp_protocol.generated.h"',
        "",
        "namespace lynx {",
        "namespace devtool {",
        "",
        "class LynxDevToolMediator;",
        "",
        f"class {class_name} : public protocol::{namespace}::Backend {{",
        " public:",
        f"  explicit {class_name}(",
        "      const std::shared_ptr<LynxDevToolMediator>& devtool_mediator);",
        f"  ~{class_name}() override = default;",
        "",
        " private:",
        "  const std::shared_ptr<LynxDevToolMediator> devtool_mediator_;",
        "};",
        "",
        "}  // namespace devtool",
        "}  // namespace lynx",
        "",
        f"#endif  // {guard}",
    ]
    return "\n".join(lines) + "\n"


def source_text(include: str, class_name: str) -> str:
    lines = [
        *license_header("//"),
        "",
        f'#include "{include}"',
        "",
        "namespace lynx {",
        "namespace devtool {",
        "",
        f"{class_name}::{class_name}(",
        "    const std::shared_ptr<LynxDevToolMediator>& devtool_mediator)",
        "    : devtool_mediator_(devtool_mediator) {}",
        "",
        "}  // namespace devtool",
        "}  // namespace lynx",
    ]
    return "\n".join(lines) + "\n"


@dataclass
class ApplyResult:
    header: Path
    source: Path
    added: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)


def apply(
    header: Path,
    class_name: str,
    methods: list[BackendMethod],
    clang_format: Optional[Path] = None,
) -> ApplyResult:
    source = header.with_suffix(".cc")
    if not source.exists():
        raise StubError(f"missing {source.name} next to {header}")
    result = ApplyResult(header=header, source=source)

    header_text = header.read_text(encoding="utf-8")
    body_start, body_end = scan.class_body(header_text, class_name)
    body = header_text[body_start:body_end]
    known = {method.method for method in methods}
    missing = scan.missing_methods(header, class_name, methods)
    result.stale = sorted(
        name
        for name in re.findall(r"\b(\w+)\s*\([^;{]*\)\s*(?:const\s*)?override\s*;", body)
        if name not in known and name != class_name
    )
    if not missing:
        return result

    declarations = "".join(
        f"  void {method.method}({method.args}) override;\n" for method in missing
    )
    at = body_start + _declaration_anchor(body, known)
    header_text = header_text[:at] + declarations + header_text[at:]

    source_text = source.read_text(encoding="utf-8")
    error_code = (
        "CDPErrorCode" if re.search(r"namespace\s+devtool\s*\{", source_text)
        else "lynx::devtool::CDPErrorCode"
    )
    definitions = "".join(
        f"void {class_name}::{method.method}({method.args}) {{\n"
        f"  responder.SendError({error_code}::MethodNotFound,\n"
        f'                      "{method.key} is not implemented");\n'
        "}\n\n"
        for method in missing
    )
    tail = re.search(r"(?:\}\s*//\s*namespace[^\n]*\n\s*)+\Z", source_text)
    if tail is None:
        raise StubError(f"cannot find the closing namespaces at the end of {source}")
    source_at = tail.start()
    source_text = source_text[:source_at] + definitions + source_text[source_at:]

    header.write_text(header_text, encoding="utf-8")
    source.write_text(source_text, encoding="utf-8")
    _format(clang_format, header, header_text, at, len(declarations))
    _format(clang_format, source, source_text, source_at, len(definitions))
    result.added = [method.key for method in missing]
    return result


def _declaration_anchor(body: str, known: set[str]) -> int:
    """Offset in `body` right after the last Backend override declaration."""

    last = None
    for match in re.finditer(r"\b(\w+)\s*\([^;{]*\)\s*override\s*;[^\n]*\n", body):
        if match.group(1) in known:
            last = match
    if last is not None:
        return last.end()
    # No override yet: append to the first public section.
    public = re.search(r"^\s*public:[^\n]*\n", body, re.MULTILINE)
    if public is None:
        raise StubError("the Backend implementation needs a `public:` section")
    following = re.search(
        r"^\s*(?:public|protected|private):", body[public.end():], re.MULTILINE
    )
    if following is None:
        return len(body.rstrip(" ")) if body.endswith("\n") else len(body)
    section = body[: public.end() + following.start()]
    return len(section.rstrip("\n")) + 1


def _format(clang_format: Optional[Path], path: Path, text: str, offset: int, length: int) -> None:
    """Format only the inserted lines, leaving existing code untouched."""

    if clang_format is None or not clang_format.exists():
        return
    first = text.count("\n", 0, offset) + 1
    last = first + text.count("\n", offset, offset + length)
    subprocess.run(
        [str(clang_format), "-i", f"--lines={first}:{last}", str(path)],
        check=True,
    )


def format_result(result: ApplyResult, display) -> str:
    lines = []
    if result.added:
        lines.append(
            style.wrap(
                f"Added {len(result.added)} Backend override(s) to "
                f"{display(result.header)} and {display(result.source)}:"
            )
        )
        lines += [
            style.item("+", f"{key}: replies MethodNotFound until implemented", "green")
            for key in result.added
        ]
    else:
        lines.append(
            style.wrap(
                f"{display(result.header)} already overrides every Backend method.",
                style.paint("=", "green") + " ",
            )
        )
    if result.stale:
        lines.append(
            style.warning("not in the Backend any more; remove by hand: ")
            + ", ".join(style.change("omitted", name) for name in result.stale)
        )
    return "\n".join(lines) + "\n"


def adapter_header(paths: Paths, namespace: str) -> Path:
    return paths.domain_agent_dir / f"inspector_{namespace}_legacy_adapter.h"


def legacy_agent_source(
    paths: Paths, contracts: contract.ContractSet, domain: str
) -> Optional[Path]:
    """The old agent's .cc: `legacy.agent`, or the file whose map names this domain."""

    legacy = contracts.domain_legacy.get(domain)
    if legacy is not None and legacy.agent:
        candidate = paths.source_root / legacy.agent
        if candidate.exists():
            return candidate
    if not paths.domain_agent_dir.exists():
        return None
    needle = f'functions_map_["{domain}.'
    for source in sorted(paths.domain_agent_dir.glob("*.cc")):
        if needle in source.read_text(encoding="utf-8", errors="ignore"):
            return source
    return None


def _related_texts(paths: Paths) -> dict[str, str]:
    related = {}
    agent_root = paths.source_root / "devtool" / "lynx_devtool" / "agent"
    mapping = {
        "mediator": agent_root / "lynx_devtool_mediator.cc",
        "executor": agent_root / "inspector_tasm_executor.cc",
    }
    for label, path in mapping.items():
        if path.exists():
            related[label] = path.read_text(encoding="utf-8", errors="ignore")
    return related


def _protocol_dir(paths: Paths) -> Path:
    return getattr(paths, "protocol_dir", paths.source_root / "devtool" / "lynx_devtool" / "protocol")


def begin_migration(
    paths: Paths,
    contracts: contract.ContractSet,
    domain: str,
    namespace: str,
    methods: list[BackendMethod],
) -> str:
    """State A: replace the old agent with a Backend that forwards through an adapter."""

    source = legacy_agent_source(paths, contracts, domain)
    if source is None:
        raise StubError(f"{domain}: no legacy agent to migrate")
    inferred = scan.infer_legacy_domain(
        domain, source.read_text(encoding="utf-8", errors="ignore"), _related_texts(paths)
    )
    forwards = {item.name: item.mediator_method for item in inferred.methods}
    class_name = f"Inspector{domain}Agent"
    header = paths.domain_agent_dir / f"inspector_{namespace}_agent.h"
    adapter = adapter_header(paths, namespace)
    _write(adapter, _adapter_header_text(domain, namespace, class_name, methods, forwards))
    _write(adapter.with_suffix(".cc"), _adapter_source_text(namespace, class_name, methods, forwards))
    previous_header = header.read_text(encoding="utf-8") if header.exists() else ""
    previous_source = source.read_text(encoding="utf-8") if source.exists() else ""
    include = header.relative_to(paths.source_root).as_posix()
    _write(header, _forwarding_header(include, class_name, namespace, methods))
    agent_cc = header.with_suffix(".cc")
    _write(agent_cc, _forwarding_source(include, class_name, namespace, methods, forwards))
    _maybe_format(getattr(paths, "clang_format", None), header, agent_cc, adapter, adapter.with_suffix(".cc"))
    notes = _inference_notes(inferred, contracts, domain)
    drafts = _write_missing_drafts(paths, contracts, domain, inferred)
    registration = _rewrite_registration(paths, domain, namespace, class_name)
    build = _insert_build_sources(
        paths,
        header.with_suffix(".cc").name,
        [adapter.name, adapter.with_suffix(".cc").name],
    )
    harness = _write_harness(paths, contracts, domain, namespace, class_name)
    display = lambda path: relative_path(paths, path)
    lines = [
        style.item("+", f"migration state A for {domain}", "green"),
        style.item("~", f"replaced {display(header)}", "yellow"),
    ]
    if previous_header:
        lines.append(style.item("~", f"previous header was {len(previous_header.splitlines())} lines", "yellow"))
    if previous_source and source != agent_cc:
        lines.append(style.item("~", f"previous source {display(source)} left in place; delete it by hand", "yellow"))
    lines += [style.item("+", f"created {display(path)}", "green") for path in (adapter, adapter.with_suffix(".cc"), *drafts, *harness)]
    lines += notes
    lines += registration
    lines += build
    lines.append(
        style.wrap(
            "Record the golden while the new agent still forwards to the old handlers: "
            "ninja -C out/<dir> devtool_agent_unittest_exec && "
            "CDPC_GOLDEN_RECORD=1 out/<dir>/devtool_agent_unittest_exec --gtest_filter=*Golden*"
        )
    )
    return "\n".join(str(line) for line in lines) + "\n"


def refresh_adapter(
    paths: Paths,
    contracts: contract.ContractSet,
    domain: str,
    namespace: str,
    methods: list[BackendMethod],
) -> str:
    """State B: keep adapter forwards only for methods that are still raw."""

    adapter = adapter_header(paths, namespace)
    source = adapter.with_suffix(".cc")
    raw_names = {method.method[:-3] for method in methods if method.method.endswith("Raw")}
    header_text = adapter.read_text(encoding="utf-8") if adapter.exists() else ""
    source_text = source.read_text(encoding="utf-8") if source.exists() else ""
    kept = []
    dropped = []
    for match in re.finditer(r"void\s+(\w+)\s*\(const cdp::RawCall& call\)", header_text):
        name = match.group(1)
        (kept if name in raw_names else dropped).append(name)
    if adapter.exists():
        _write(adapter, _filter_declarations(header_text, raw_names))
        _write(source, _filter_definitions(source_text, raw_names))
    legacy = contracts.domain_legacy.get(domain)
    migrated = legacy.migrated if legacy else []
    lines = [style.item("~", f"migration state B for {domain}: rewrote the adapter", "yellow")]
    if dropped:
        lines.append(
            style.warning("adapter no longer forwards; delete the old handlers by hand: ")
            + ", ".join(dropped)
        )
    if migrated:
        lines.append(style.item("=", f"migrated: {', '.join(migrated)}", "green"))
    return "\n".join(lines) + "\n"


def finish_migration(
    paths: Paths,
    contracts: contract.ContractSet,
    domain: str,
    namespace: str,
    methods: list[BackendMethod],
    backends: list[tuple[Path, str]],
) -> str:
    """State C: delete the adapter and the domain-level legacy block."""

    adapter = adapter_header(paths, namespace)
    removed = []
    for path in (adapter, adapter.with_suffix(".cc")):
        if path.exists():
            path.unlink()
            removed.append(relative_path(paths, path))
    support = getattr(paths, "support_file", None)
    stripped = False
    if support is not None and Path(support).exists():
        text = Path(support).read_text(encoding="utf-8")
        updated = strip_domain_legacy(text, domain)
        if updated != text:
            Path(support).write_text(updated, encoding="utf-8")
            stripped = True
    _strip_function_map(paths, domain)
    override_report = ""
    if len(backends) == 1:
        header, class_name = backends[0]
        result = apply(header, class_name, methods, getattr(paths, "clang_format", None))
        override_report = format_result(result, lambda path: relative_path(paths, path))
    lines = [style.item("+", f"migration state C for {domain}", "green")]
    lines += [style.item("-", f"deleted {path}", "red") for path in removed]
    if stripped:
        lines.append(style.item("-", f"removed the domain legacy block from {support}", "red"))
    lines.append(
        style.wrap(
            "Delete the old mediator and executor handlers by hand, then fill the "
            "typed Backend bodies. Method-level legacy blocks stay while validation is legacy."
        )
    )
    return "\n".join(lines) + "\n" + override_report


def strip_domain_legacy(text: str, domain: str) -> str:
    """Drop the indented `legacy:` mapping under a top-level domain key."""

    lines = text.splitlines(keepends=True)
    start = None
    for index, line in enumerate(lines):
        if line.startswith(f"{domain}:"):
            start = index
            break
    if start is None:
        return text
    legacy_at = None
    for index in range(start + 1, len(lines)):
        if lines[index].startswith(f"{domain}:") or (
            lines[index][:1].isalpha() and not lines[index].startswith(" ")
        ):
            break
        if lines[index].startswith("  legacy:"):
            legacy_at = index
            break
    if legacy_at is None:
        return text
    end = legacy_at + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() == "" or line.startswith("    ") or line.startswith("\t"):
            end += 1
            continue
        break
    del lines[legacy_at:end]
    return "".join(lines)


def _strip_function_map(paths: Paths, domain: str) -> None:
    source = paths.domain_agent_dir / f"inspector_{codegen.snake_case(domain)}_agent.cc"
    if not source.exists():
        return
    text = source.read_text(encoding="utf-8")
    kept = [
        line
        for line in text.splitlines(keepends=True)
        if f'functions_map_["{domain}.' not in line
    ]
    source.write_text("".join(kept), encoding="utf-8")


def _inference_notes(inferred: scan.InferredDomain, contracts: contract.ContractSet, domain: str) -> list[str]:
    """Differences between the regex draft and docs that already exist. Docs are not overwritten."""

    notes = []
    for todo in inferred.todos:
        notes.append(style.item("!", f"TODO(cdpc): {todo}", "yellow"))
    for method in inferred.methods:
        for todo in method.todos:
            notes.append(style.item("!", f"TODO(cdpc): {todo}", "yellow"))
        doc = contracts.methods.get((domain, method.name))
        if doc is None:
            notes.append(style.item("+", f"{domain}.{method.name}: no docs yet", "green"))
            continue
        if method.mediator_method is None:
            notes.append(
                style.item("~", f"{domain}.{method.name}: no mediator forward was inferred", "yellow")
            )
        documented = doc.legacy.thread if doc.legacy is not None else "none"
        if method.thread != "none" and documented != method.thread:
            notes.append(
                style.item(
                    "~",
                    f"{domain}.{method.name}: inferred thread {method.thread}, docs say {documented}",
                    "yellow",
                )
            )
    return notes


def _write_missing_drafts(
    paths: Paths, contracts: contract.ContractSet, domain: str, inferred: scan.InferredDomain
) -> list[Path]:
    written = []
    docs = _protocol_dir(paths) / "custom_cdp_docs" / domain / "methods"
    for method in inferred.methods:
        if contracts.methods.get((domain, method.name)) is not None:
            continue
        docs.mkdir(parents=True, exist_ok=True)
        path = docs / f"{method.name}.yaml"
        path.write_text(_draft_method_yaml(method), encoding="utf-8")
        written.append(path)
    return written


def _draft_method_yaml(method: scan.InferredMethod) -> str:
    lines = [
        f"summary: TODO(cdpc): inferred from {method.handler}",
        "parameters:",
    ]
    if not method.params:
        lines.append("  []")
    for param in method.params:
        type_name = param.type_name or "string"
        lines.append(f"  - name: {param.name}")
        lines.append(f"    type: {type_name}")
        if param.optional:
            lines.append("    optional: true")
        if param.todo:
            lines.append(f"    # TODO(cdpc): {param.todo}")
    lines += [
        "legacy:",
        f"  validate: {method.validate}",
        f"  thread: {method.thread}",
    ]
    if method.errors:
        lines.append("  errors:")
        for error in method.errors:
            lines += [
                f"    - field: {error.field or '\"\"'}",
                f"      reason: {error.reason}",
                f"      code: {error.code}",
                f"      message: {error.message}",
            ]
    else:
        lines.append("  # TODO(cdpc): no SendError call was recognized")
    lines.append("")
    return "\n".join(lines)


def _adapter_header_text(
    domain: str,
    namespace: str,
    class_name: str,
    methods: list[BackendMethod],
    forwards: dict[str, Optional[str]],
) -> str:
    include = f"devtool/lynx_devtool/agent/domain_agent/inspector_{namespace}_legacy_adapter.h"
    guard = "".join(c if c.isalnum() else "_" for c in include).upper() + "_"
    declarations = []
    for method in methods:
        if not method.method.endswith("Raw"):
            continue
        declarations.append(f"  void {method.method[:-3]}(const cdp::RawCall& call);")
    body = "\n".join(declarations)
    return "\n".join([
        *license_header("//"),
        "//",
        "// Generated by `tools/cdpc stubs`. Forwards unmigrated methods to the old handlers.",
        "",
        f"#ifndef {guard}",
        f"#define {guard}",
        "",
        "#include <memory>",
        "",
        '#include "devtool/base_devtool/native/public/cdp_typed_responder.h"',
        "",
        "namespace lynx {",
        "namespace devtool {",
        "",
        "class LynxDevToolMediator;",
        "",
        f"class {class_name}LegacyAdapter {{",
        " public:",
        f"  explicit {class_name}LegacyAdapter(",
        "      const std::shared_ptr<LynxDevToolMediator>& devtool_mediator);",
        "",
        body,
        "",
        " private:",
        "  const std::shared_ptr<LynxDevToolMediator> devtool_mediator_;",
        "};",
        "",
        "}  // namespace devtool",
        "}  // namespace lynx",
        "",
        f"#endif  // {guard}",
        "",
    ])


def _adapter_source_text(
    namespace: str,
    class_name: str,
    methods: list[BackendMethod],
    forwards: dict[str, Optional[str]],
) -> str:
    include = f"devtool/lynx_devtool/agent/domain_agent/inspector_{namespace}_legacy_adapter.h"
    lines = [
        *license_header("//"),
        "//",
        "// Generated by `tools/cdpc stubs`. Forwards unmigrated methods to the old handlers.",
        "",
        f'#include "{include}"',
        "",
        '#include "devtool/lynx_devtool/agent/lynx_devtool_mediator.h"',
        "",
        "namespace lynx {",
        "namespace devtool {",
        "",
        f"{class_name}LegacyAdapter::{class_name}LegacyAdapter(",
        "    const std::shared_ptr<LynxDevToolMediator>& devtool_mediator)",
        "    : devtool_mediator_(devtool_mediator) {}",
        "",
    ]
    for method in methods:
        if not method.method.endswith("Raw"):
            continue
        plain = method.method[:-3]
        wire = method.key.split(".", 1)[1]
        target = forwards.get(wire)
        lines.append(f"void {class_name}LegacyAdapter::{plain}(const cdp::RawCall& call) {{")
        if target:
            lines += [
                f"  devtool_mediator_->{target}(call.responder, call.message[\"params\"]);",
            ]
        else:
            lines += [
                f"  // TODO(cdpc): could not infer the old handler for {method.key}",
                "  call.responder->SendError(CDPErrorCode::MethodNotFound,",
                f'                         "{method.key} is not migrated");',
            ]
        lines += ["}", ""]
    lines += ["}  // namespace devtool", "}  // namespace lynx", ""]
    return "\n".join(lines)


def _forwarding_header(
    include: str, class_name: str, namespace: str, methods: list[BackendMethod]
) -> str:
    guard = "".join(c if c.isalnum() else "_" for c in include).upper() + "_"
    overrides = "\n".join(
        f"  void {method.method}({method.args}) override;" for method in methods
    )
    return "\n".join([
        *license_header("//"),
        "",
        f"#ifndef {guard}",
        f"#define {guard}",
        "",
        "#include <memory>",
        "",
        f'#include "devtool/lynx_devtool/agent/domain_agent/inspector_{namespace}_legacy_adapter.h"',
        '#include "devtool/lynx_devtool/protocol/cdp_protocol.generated.h"',
        "",
        "namespace lynx {",
        "namespace devtool {",
        "",
        "class LynxDevToolMediator;",
        "",
        f"class {class_name} : public protocol::{namespace}::Backend {{",
        " public:",
        f"  explicit {class_name}(",
        "      const std::shared_ptr<LynxDevToolMediator>& devtool_mediator);",
        f"  ~{class_name}() override = default;",
        "",
        overrides,
        "",
        " private:",
        f"  {class_name}LegacyAdapter legacy_;",
        "};",
        "",
        "}  // namespace devtool",
        "}  // namespace lynx",
        "",
        f"#endif  // {guard}",
        "",
    ])


def _forwarding_source(
    include: str,
    class_name: str,
    namespace: str,
    methods: list[BackendMethod],
    forwards: dict[str, Optional[str]],
) -> str:
    lines = [
        *license_header("//"),
        "",
        f'#include "{include}"',
        "",
        "namespace lynx {",
        "namespace devtool {",
        "",
        f"{class_name}::{class_name}(",
        "    const std::shared_ptr<LynxDevToolMediator>& devtool_mediator)",
        "    : legacy_(devtool_mediator) {}",
        "",
    ]
    for method in methods:
        lines.append(f"void {class_name}::{method.method}({method.args}) {{")
        if method.method.endswith("Raw"):
            lines.append(f"  legacy_.{method.method[:-3]}(call);")
        else:
            lines += [
                "  responder.SendError(CDPErrorCode::MethodNotFound,",
                f'                      "{method.key} is not implemented");',
            ]
        lines += ["}", ""]
    lines += ["}  // namespace devtool", "}  // namespace lynx", ""]
    return "\n".join(lines)


def _rewrite_registration(paths: Paths, domain: str, namespace: str, class_name: str) -> list[str]:
    source = getattr(paths, "registration_source", None)
    if source is None or not Path(source).exists():
        return [style.item("!", f"registration file not found; wrap {class_name} in CreateAgent by hand", "yellow")]
    text = Path(source).read_text(encoding="utf-8")
    pattern = re.compile(
        rf'RegisterAgent\(\s*"{re.escape(domain)}"\s*,\s*'
        rf"std::make_unique<{re.escape(class_name)}>\(([^)]*)\)\s*\)",
        re.DOTALL,
    )
    rewritten, count = pattern.subn(
        lambda match: (
            f'RegisterAgent(\n'
            f'      "{domain}",\n'
            f"      protocol::{namespace}::CreateAgent(\n"
            f"          std::make_unique<{class_name}>({match.group(1).strip()})))"
        ),
        text,
    )
    if count:
        Path(source).write_text(rewritten, encoding="utf-8")
    return [style.item("~" if count else "!", f"rewrote {count} RegisterAgent site(s) for {domain}", "yellow")]


def _insert_build_sources(paths: Paths, agent_name: str, additions: list[str]) -> list[str]:
    notes = []
    found = False
    for build in sorted(paths.source_root.rglob("BUILD.gn")):
        text = build.read_text(encoding="utf-8")
        match = re.search(rf'"([^"]*{re.escape(agent_name)})"', text)
        if match is None:
            continue
        found = True
        prefix = match.group(1)[: -len(agent_name)]
        updated = text
        for name in additions:
            entry = f'"{prefix}{name}"'
            if entry in updated:
                continue
            anchor = match.group(0)
            updated = updated.replace(anchor, anchor + ",\n    " + entry, 1)
        if updated != text:
            build.write_text(updated, encoding="utf-8")
            notes.append(style.item("+", f"added sources in {relative_path(paths, build)}", "green"))
    if not found:
        notes.append(style.item("!", f"no BUILD.gn lists {agent_name}; add the adapter sources by hand", "yellow"))
    return notes


def _write_harness(
    paths: Paths,
    contracts: contract.ContractSet,
    domain: str,
    namespace: str,
    class_name: str,
) -> list[Path]:
    protocol = _protocol_dir(paths)
    directory = protocol / "testdata" / "golden"
    directory.mkdir(parents=True, exist_ok=True)
    cases = golden.build_matrix(contracts, domain)
    requests = directory / f"{domain}.requests.yaml"
    jsonl = directory / f"{domain}.golden.jsonl"
    requests.write_text(golden.render_requests_yaml(cases), encoding="utf-8")
    if not jsonl.exists():
        body = []
        for case in sorted(cases, key=lambda item: item["case"]):
            stored = {
                "case": case["case"],
                "env": case.get("env", {}),
                "request": case["request"],
            }
            if case.get("pre"):
                stored["pre"] = case["pre"]
            body.append(json_dumps(stored))
        jsonl.write_text("".join(line + "\n" for line in body), encoding="utf-8")
    volatile = []
    spec = contracts.domain_golden.get(domain)
    if spec is not None:
        volatile = [item.path for item in spec.volatile]
    unittest_path = paths.domain_agent_dir / f"inspector_{namespace}_golden_unittest.cc"
    golden_file = jsonl.relative_to(paths.source_root).as_posix() if _is_relative(jsonl, paths.source_root) else jsonl.as_posix()
    unittest_path.write_text(
        golden.render_unittest(domain, namespace, class_name, golden_file, volatile),
        encoding="utf-8",
    )
    _maybe_format(getattr(paths, "clang_format", None), unittest_path)
    return [requests, jsonl, unittest_path]


def _is_relative(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def json_dumps(value: dict) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _filter_declarations(text: str, raw_names: set[str]) -> str:
    kept = []
    for line in text.splitlines(keepends=True):
        match = re.search(r"void\s+(\w+)\s*\(const cdp::RawCall&", line)
        if match and match.group(1) not in raw_names:
            continue
        kept.append(line)
    return "".join(kept)


def _filter_definitions(text: str, raw_names: set[str]) -> str:
    parts = re.split(r"(?=^void\s+\w+LegacyAdapter::)", text, flags=re.MULTILINE)
    kept = [parts[0]]
    for part in parts[1:]:
        match = re.match(r"void\s+\w+LegacyAdapter::(\w+)\s*\(", part)
        if match and match.group(1) not in raw_names:
            continue
        kept.append(part)
    return "".join(kept)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _maybe_format(clang_format: Optional[Path], *paths: Path) -> None:
    if clang_format is None or not Path(clang_format).exists():
        return
    subprocess.run([str(clang_format), "-i", *[str(path) for path in paths]], check=False)

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

from cdpc import codegen, contract, scan, style
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
    """Add the missing overrides of `domain`'s Backend; returns the report."""

    namespace, methods = codegen.backend_methods(contracts, domain)
    qualified = f"protocol::{namespace}"
    found = scan.find_backend_classes([paths.source_root / "devtool"], [qualified])[qualified]
    created: list[Path] = []
    if not found:
        header, class_name = scaffold(paths, domain, namespace)
        created = [header, header.with_suffix(".cc")]
    else:
        header, class_name = scan.find_backend_class(
            [paths.source_root / "devtool"], qualified
        )
    result = apply(header, class_name, methods, paths.clang_format)
    display = lambda path: relative_path(paths, path)
    report = "".join(
        style.item("+", f"created {display(path)}", "green") + "\n" for path in created
    )
    if created:
        report += style.wrap(
            f"Register it with RegisterAgent(\"{domain}\", {qualified}::CreateAgent("
            f"std::make_unique<{class_name}>(devtool_mediator_))) and add both files "
            "to agent/BUILD.gn.",
        ) + "\n"
    return report + format_result(result, display)


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

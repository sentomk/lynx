# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Scanners over Lynx sources.

Each scanner reads hand-written code and never edits it: CDP method tables in
domain agents and PrimJS, domain registration scopes, the Lynx version, event
names sent by string literal, and classes implementing a generated Backend.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from cdpc import contract, style
from cdpc.codegen import BackendMethod
from cdpc.repo import CdpcError, Paths, relative_path


METHOD_NAME_PATTERN = r"[A-Za-z][A-Za-z0-9]*\.[A-Za-z][A-Za-z0-9_]*"


METHOD_RE = re.compile(
    rf'functions_map_\["({METHOD_NAME_PATTERN})"\]'
)


CALL_METHOD_FUNCTION_RE = re.compile(
    r"\bvoid\s+[A-Za-z_]\w*::CallMethod\s*\([^)]*\)\s*\{"
)


DIRECT_METHOD_RE = re.compile(
    rf'\bif\s*\(\s*method\s*==\s*"({METHOD_NAME_PATTERN})"\s*\)'
)


PRIMJS_FUNCTION_RE = re.compile(
    r"const\s+debug_function_type\s*&\s*GetDebugFunctionMap\s*\(\)\s*\{",
    re.MULTILINE,
)


PRIMJS_METHOD_RE = re.compile(rf'\{{\s*"({METHOD_NAME_PATTERN})"\s*,')


LYNX_VERSION_RE = re.compile(
    r"^#define\s+LYNX_VERSION\s+tasm::V_([0-9]+)_([0-9]+)\b",
    re.MULTILINE,
)


REGISTER_FUNCTION_RE = re.compile(
    r"void\s+LynxDevToolNG::Register(Global|Instance)DomainAgents\s*\([^)]*\)\s*\{",
    re.MULTILINE,
)


REGISTER_AGENT_RE = re.compile(
    r'(?:\bglobal_dispatcher\.)?RegisterAgent\s*\(\s*"([A-Za-z][A-Za-z0-9]*)"'
)


EVENT_LITERAL_RE = re.compile(r'"([A-Z][A-Za-z0-9]*\.[a-z][A-Za-z0-9_]*)"')


EVENT_SOURCE_SUFFIXES = frozenset({".cc", ".h", ".mm", ".m", ".java", ".kt", ".ets"})


@dataclass(frozen=True)
class LocalMethod:
    domain: str
    method: str
    source: str


def scan_local_methods(
    paths: Paths,
    previous_sources: Optional[dict[tuple[str, str], str]] = None,
    require_primjs: bool = True,
) -> list[LocalMethod]:
    """Methods from domain-agent method tables and the PrimJS method map.

    Without PrimJS sources and `require_primjs`, PrimJS rows come from
    `previous_sources`, the method sources recorded in the checked-in manifest.
    """

    if not paths.domain_agent_dir.exists():
        raise CdpcError(f"missing domain agent directory: {paths.domain_agent_dir}")

    methods: dict[tuple[str, str], LocalMethod] = {}
    for source_file in sorted(paths.domain_agent_dir.glob("*.cc")):
        if source_file.name.endswith("_unittest.cc"):
            continue
        text = source_file.read_text(encoding="utf-8")
        declared_methods = METHOD_RE.findall(text)
        for match in CALL_METHOD_FUNCTION_RE.finditer(text):
            body = extract_braced_block(text, match.end() - 1)
            declared_methods.extend(DIRECT_METHOD_RE.findall(body))
        for full_method in declared_methods:
            domain, method = full_method.split(".", 1)
            if domain in contract.ENGINE_DOMAINS:
                continue
            source = relative_path(paths, source_file)
            add_local_method(methods, domain, method, source, full_method)

    if paths.primjs_protocols_source.exists() or require_primjs:
        scan_primjs_methods(paths, methods)
    else:
        reuse_primjs_methods(paths, methods, previous_sources)

    return [methods[key] for key in sorted(methods)]


def reuse_primjs_methods(
    paths: Paths,
    methods: dict[tuple[str, str], LocalMethod],
    previous_sources: Optional[dict[tuple[str, str], str]],
) -> None:
    """Keep PrimJS rows from the checked-in manifest when its source is absent.

    Local checkouts may not fetch PrimJS. The reused rows are unverified, so CI
    passes --require-primjs to scan the real method map instead.
    """

    source = relative_path(paths, paths.primjs_protocols_source)
    reused = 0
    if previous_sources is not None:
        for (domain, method), method_source in previous_sources.items():
            if method_source == source:
                add_local_method(methods, domain, method, source, f"{domain}.{method}")
                reused += 1
    print(
        style.warning(
            f"{source} is missing; reused {reused} PrimJS method rows from "
            "the checked-in manifest without verifying them. "
            "Pass --require-primjs to fail instead."
        ),
        file=sys.stderr,
    )


def scan_primjs_methods(
    paths: Paths, methods: dict[tuple[str, str], LocalMethod]
) -> None:
    source_file = paths.primjs_protocols_source
    if not source_file.exists():
        raise CdpcError(f"missing PrimJS CDP method map: {source_file}")

    text = source_file.read_text(encoding="utf-8")
    match = PRIMJS_FUNCTION_RE.search(text)
    if not match:
        raise CdpcError(
            f"cannot find GetDebugFunctionMap() in {relative_path(paths, source_file)}"
        )

    body = extract_braced_block(text, match.end() - 1)
    source = relative_path(paths, source_file)
    found = False
    for full_method in PRIMJS_METHOD_RE.findall(body):
        domain, method = full_method.split(".", 1)
        add_local_method(methods, domain, method, source, full_method)
        found = True

    if not found:
        raise CdpcError(
            f"no CDP methods found in {relative_path(paths, source_file)}"
        )


def add_local_method(
    methods: dict[tuple[str, str], LocalMethod],
    domain: str,
    method: str,
    source: str,
    full_method: str,
) -> None:
    key = (domain, method)
    existing = methods.get(key)
    if existing and existing.source != source:
        raise CdpcError(
            f"duplicate method declaration for {full_method}: "
            f"{existing.source} and {source}"
        )
    methods[key] = LocalMethod(domain=domain, method=method, source=source)


def scan_domain_scopes(source_file: Path) -> dict[str, str]:
    if not source_file.exists():
        raise CdpcError(f"missing domain registration source: {source_file}")
    text = source_file.read_text(encoding="utf-8")

    domain_scopes: dict[str, set[str]] = {}
    for match in REGISTER_FUNCTION_RE.finditer(text):
        scope = "global" if match.group(1) == "Global" else "instance"
        body = extract_braced_block(text, match.end() - 1)
        for domain in REGISTER_AGENT_RE.findall(body):
            domain_scopes.setdefault(domain, set()).add(scope)

    scopes: dict[str, str] = {}
    for domain, found_scopes in domain_scopes.items():
        if found_scopes == {"global"}:
            scopes[domain] = "global"
        elif found_scopes == {"instance"}:
            scopes[domain] = "instance"
        elif found_scopes == {"global", "instance"}:
            scopes[domain] = "global-and-instance"
        else:
            raise CdpcError(f"unexpected scope set for {domain}: {found_scopes}")
    return scopes


def extract_braced_block(text: str, open_brace_index: int) -> str:
    if text[open_brace_index] != "{":
        raise CdpcError("internal parser error: expected opening brace")
    depth = 0
    for index in range(open_brace_index, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace_index + 1 : index]
    raise CdpcError("unterminated function body while scanning domain registration")


def scan_current_lynx_version(source_file: Path) -> str:
    """Return LYNX_VERSION from config.h as the manifest version string."""

    if not source_file.exists():
        raise CdpcError(f"missing Lynx version source: {source_file}")

    text = source_file.read_text(encoding="utf-8")
    match = LYNX_VERSION_RE.search(text)
    if not match:
        raise CdpcError(
            f"cannot find LYNX_VERSION in {source_file}; expected tasm::V_<major>_<minor>"
        )
    return f"{int(match.group(1))}.{int(match.group(2))}"


def scan_event_references(
    roots: Iterable[Path], events: Iterable[tuple[str, str]]
) -> dict[tuple[str, str], Path]:
    """Events from `events` named by a string literal in non-test sources.

    A literal such as "DOM.documentUpdated" is how Lynx code names the events
    it sends. It is a heuristic: a literal that is only compared against, or a
    disabled code path, also counts.
    """

    known = set(events)
    found: dict[tuple[str, str], Path] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix not in EVENT_SOURCE_SUFFIXES or _is_test_or_generated(path):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for name in EVENT_LITERAL_RE.findall(text):
                key = tuple(name.split(".", 1))
                if key in known:
                    found.setdefault(key, path)
    return found


def _is_test_or_generated(path: Path) -> bool:
    name = path.name.lower()
    parts = {part.lower() for part in path.parts}
    return (
        "unittest" in name
        or "_test." in name
        or ".generated." in name
        or bool(parts & {"test", "tests", "testing", "testdata", "androidtest"})
    )


def find_backend_classes(
    roots: Iterable[Path], namespaces: Iterable[str]
) -> dict[str, list[tuple[Path, str]]]:
    """Classes derived from `<namespace>::Backend`, per namespace, in one scan."""

    patterns = {
        namespace: re.compile(
            rf"\bclass\s+(\w+)\s*(?:final\s*)?:[^{{;]*\b{re.escape(namespace)}::Backend\b"
        )
        for namespace in namespaces
    }
    found: dict[str, list[tuple[Path, str]]] = {namespace: [] for namespace in patterns}
    for root in roots:
        for header in sorted(root.rglob("*.h")):
            if ".generated." in header.name:
                continue
            text = header.read_text(encoding="utf-8", errors="ignore")
            if "::Backend" not in text:
                continue
            for namespace, pattern in patterns.items():
                found[namespace] += [(header, match.group(1)) for match in pattern.finditer(text)]
    return found


def find_backend_class(roots: Iterable[Path], namespace: str) -> tuple[Path, str]:
    """The only header declaring a class derived from `<namespace>::Backend`."""

    found = find_backend_classes(roots, [namespace])[namespace]
    if len(found) != 1:
        listed = ", ".join(f"{cls} in {path}" for path, cls in found) or "none"
        raise CdpcError(
            f"expected exactly one class implementing {namespace}::Backend, found {listed}"
        )
    return found[0]


def missing_methods(
    header: Path, class_name: str, methods: list[BackendMethod]
) -> list[BackendMethod]:
    """Backend methods the class does not declare yet."""

    text = header.read_text(encoding="utf-8")
    start, end = class_body(text, class_name)
    body = text[start:end]
    return [method for method in methods if not re.search(rf"\b{method.method}\s*\(", body)]


def class_body(text: str, class_name: str) -> tuple[int, int]:
    match = re.search(rf"\bclass\s+{re.escape(class_name)}\b[^{{;]*\{{", text)
    if match is None:
        raise CdpcError(f"cannot find the body of class {class_name}")
    depth = 0
    for index in range(match.end() - 1, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return match.end(), index
    raise CdpcError(f"unbalanced braces in class {class_name}")

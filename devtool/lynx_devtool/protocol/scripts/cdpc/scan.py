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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from cdpc import contract, style
from cdpc.codegen import BackendMethod
from cdpc.repo import CdpcError, EnvError, Paths, relative_path


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
        raise EnvError(
            f"missing PrimJS CDP method map: {source_file}; run `tools/hab sync .`"
        )

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


# ---------------------------------------------------------------------------
# Legacy migration inference. Regex only: anything uncertain becomes a TODO.


HANDLER_ASSIGN_RE = re.compile(
    r'functions_map_\["([A-Za-z0-9]+\.([A-Za-z0-9_]+))"\]\s*=\s*&\w+::(\w+)\s*;'
)
PARAMS_READ_RE = re.compile(r'(?:params|message)\[\s*"([A-Za-z0-9_]+)"\s*\]')
RESULT_WRITE_RE = re.compile(r'result\[\s*"([A-Za-z0-9_]+)"\s*\]')
SEND_ERROR_RE = re.compile(
    r'SendError\(\s*CDPErrorCode::(\w+)\s*,\s*"([^"\\]*)"\s*\)'
)
MEMBER_GUARD_RE = re.compile(r'isMember\(\s*"([A-Za-z0-9_]+)"\s*\)')
MEDIATOR_CALL_RE = re.compile(r'devtool_mediator_->(\w+)\s*\(')
EXECUTOR_CALL_RE = re.compile(r'executor->(\w+)\s*\(')
FUNCTION_DEF_RE = re.compile(r'\b(\w+)::(\w+)\s*\([^;]*\)\s*\{')
DECLARE_CDP_RE = re.compile(r"DECLARE_DEVTOOL_CDP_METHOD\((\w+)\)")
CALL_RE_TEMPLATE = r"\b{name}\s*\("


@dataclass
class InferredParam:
    name: str
    type_name: Optional[str]
    optional: bool
    todo: Optional[str] = None


@dataclass
class InferredError:
    field: str
    reason: str
    code: str
    message: str


@dataclass
class InferredMethod:
    name: str
    handler: str
    thread: str
    validate: str
    params: list[InferredParam] = field(default_factory=list)
    errors: list[InferredError] = field(default_factory=list)
    todos: list[str] = field(default_factory=list)
    # Mediator method the agent forwards to, when the regex can see it.
    mediator_method: Optional[str] = None


@dataclass
class InferredDomain:
    order: list[str]
    methods: list[InferredMethod]
    todos: list[str] = field(default_factory=list)


def infer_legacy_domain(
    domain: str, agent_text: str, related: Optional[dict[str, str]] = None
) -> InferredDomain:
    """Draft `legacy` fields from an old agent and the handlers it forwards to.

    `related` maps a file label to source text (mediator, executor). Inference
    follows one mediator hop and one executor hop. A field it cannot type is
    left as TODO(cdpc).
    """

    related = related or {}
    found = InferredDomain(order=[], methods=[])
    for match in HANDLER_ASSIGN_RE.finditer(agent_text):
        full, method, handler = match.group(1), match.group(2), match.group(3)
        if not full.startswith(domain + "."):
            continue
        found.order.append(method)
        bodies = [function_body(agent_text, handler)]
        thread = "none"
        validate = "decode"
        mediator_call = _first(MEDIATOR_CALL_RE, bodies[0])
        if mediator_call and "mediator" in related:
            mediator_body = function_body(related["mediator"], mediator_call)
            bodies.append(mediator_body)
            if "RunOnTASMThread" in mediator_body:
                thread = "tasm"
                validate = "handler"
            elif "RunOnUIThread" in mediator_body:
                thread = "ui"
                validate = "handler"
            elif "RunOnDevToolThread" in mediator_body:
                thread = "devtool"
                validate = "handler"
            executor_call = _first(EXECUTOR_CALL_RE, mediator_body)
            if executor_call and "executor" in related:
                bodies.append(function_body(related["executor"], executor_call))
        elif mediator_call:
            found.todos.append(
                f"{method}: mediator call {mediator_call} but no mediator source was provided"
            )
        item = InferredMethod(
            name=method,
            handler=handler,
            thread=thread,
            validate=validate,
            mediator_method=mediator_call,
        )
        combined = "\n".join(bodies)
        item.params = _infer_params(combined)
        item.errors = _infer_errors(combined, item.params)
        if not item.params and "params[" in combined:
            item.todos.append(f"{method}: params reads were not recognized")
        found.methods.append(item)
    if not found.order:
        found.todos.append(f"no functions_map_ entries for {domain}")
    return found


def function_body(text: str, name: str) -> str:
    """Body of the first `::name(...) {` definition, or empty when it is absent."""

    for match in FUNCTION_DEF_RE.finditer(text):
        if match.group(2) != name:
            continue
        try:
            return extract_braced_block(text, match.end() - 1)
        except CdpcError:
            return ""
    return ""


def _first(pattern: re.Pattern, text: str) -> Optional[str]:
    match = pattern.search(text)
    return match.group(1) if match else None


def _infer_params(body: str) -> list[InferredParam]:
    optional = set(MEMBER_GUARD_RE.findall(body))
    params = []
    seen = set()
    for name in PARAMS_READ_RE.findall(body):
        if name in seen:
            continue
        seen.add(name)
        window = _window(body, name)
        type_name = _type_from_window(window)
        todo = None if type_name else "type not recognized from asXxx/isObject"
        params.append(InferredParam(name, type_name, name in optional or "empty()" in window, todo))
    return params


def _window(body: str, name: str) -> str:
    index = body.find(f'"{name}"')
    if index < 0:
        return ""
    return body[index : index + 180]


def _type_from_window(window: str) -> Optional[str]:
    if ".isObject()" in window:
        return "object"
    if ".isArray()" in window:
        return "array"
    if ".asBool()" in window:
        return "boolean"
    if ".asDouble()" in window:
        return "number"
    if ".asInt()" in window or ".asInt64()" in window or ".asUInt64()" in window:
        return "integer"
    if ".asString()" in window:
        return "string"
    return None


def _infer_errors(body: str, params: list[InferredParam]) -> list[InferredError]:
    errors = []
    for code, message in SEND_ERROR_RE.findall(body):
        field = ""
        reason = "type"
        for param in params:
            if param.name and param.name in message:
                field = param.name
                break
        if "must be an object" in message or "unexpected type" in message or "isObject" in body:
            reason = "type"
        elif "missing" in message.lower():
            reason = "missing"
        errors.append(InferredError(field=field, reason=reason, code=code, message=message))
    return errors


def parse_removed_declarations(diff_text: str) -> list[str]:
    """Method names whose DECLARE_DEVTOOL_CDP_METHOD line was deleted."""

    removed = []
    for line in diff_text.splitlines():
        if not line.startswith("-") or line.startswith("---"):
            continue
        match = DECLARE_CDP_RE.search(line[1:])
        if match and match.group(1) not in removed:
            removed.append(match.group(1))
    return removed


def find_removed_callers(root: Path, names: Iterable[str], display) -> list[str]:
    """`file:line: calls removed Name` for each call under `root`, including tests."""

    names = list(names)
    if not names or not root.exists():
        return []
    patterns = [(name, re.compile(CALL_RE_TEMPLATE.format(name=re.escape(name)))) for name in names]
    found = []
    for path in sorted(root.rglob("*")):
        if path.suffix not in {".h", ".cc", ".mm"} or ".generated." in path.name:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        relative = display(path)
        for number, line in enumerate(lines, 1):
            if "DECLARE_DEVTOOL_CDP_METHOD" in line:
                continue
            code = line.split("//", 1)[0]
            for name, pattern in patterns:
                if pattern.search(code):
                    found.append(f"{relative}:{number}: calls removed {name}")
    return found


def merge_base(source_root: Path) -> Optional[str]:
    """Commit to diff against when looking for declarations this branch removed."""

    import os
    import subprocess

    override = os.environ.get("CDPC_MERGE_BASE")
    if override:
        return override
    for candidate in ("origin/develop", "develop"):
        try:
            return subprocess.check_output(
                ["git", "merge-base", "HEAD", candidate],
                cwd=source_root,
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    return None


def removed_declarations_since(source_root: Path, base: str) -> list[str]:
    import subprocess

    diff = subprocess.check_output(
        ["git", "diff", "-U0", base, "--", "devtool"],
        cwd=source_root,
        text=True,
        stderr=subprocess.DEVNULL,
    )
    return parse_removed_declarations(diff)


def declarations_still_present(root: Path, names: Iterable[str]) -> set[str]:
    """Names that still have a method declaration under `root`.

    A typed override (`void Name(...) override;`) counts, so renaming a
    DECLARE_DEVTOOL_CDP_METHOD into a Backend method does not look like a
    deletion. Calls such as `executor->Name(...)` do not.
    """

    pending = set(names)
    if not pending or not root.exists():
        return set()
    patterns = {
        name: re.compile(
            rf"DECLARE_DEVTOOL_CDP_METHOD\(\s*{re.escape(name)}\s*\)"
            rf"|\b{re.escape(name)}\s*\([^;{{]*\)\s*(?:const\s*)?override\s*;"
            rf"|\b{re.escape(name)}\s*\([^;{{]*\)\s*(?:const\s*)?=\s*0\s*;"
            rf"|(?:^|\n)\s*(?:virtual\s+)?[\w:<>,*&]+(?:\s+[\w:<>,*&]+)*\s+"
            rf"{re.escape(name)}\s*\([^;{{]*\)\s*(?:const\s*)?;",
            re.MULTILINE,
        )
        for name in pending
    }
    found: set[str] = set()
    for path in sorted(root.rglob("*.h")):
        if ".generated." in path.name:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name in list(pending):
            if patterns[name].search(text):
                found.add(name)
                pending.discard(name)
        if not pending:
            break
    return found


def scan_removed_symbols(paths: Paths) -> list[str]:
    """Call sites of CDP methods whose declaration this branch deleted.

    Compares `devtool/` with `git merge-base HEAD origin/develop` (or
    `CDPC_MERGE_BASE`). A name that is still declared on another class, such
    as `Enable`, is not reported. The message is `file:line: calls removed Name`.
    """

    import subprocess

    base = merge_base(paths.source_root)
    if not base:
        return []
    devtool = paths.source_root / "devtool"
    try:
        names = removed_declarations_since(paths.source_root, base)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    gone = [name for name in names if name not in declarations_still_present(devtool, names)]
    return find_removed_callers(devtool, gone, lambda path: relative_path(paths, path))

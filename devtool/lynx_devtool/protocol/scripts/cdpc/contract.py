# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""CDP contract model built from custom_cdp_docs and the vendored upstream schema.

custom_cdp_docs YAML files are the contract for Lynx-extension entries. Fields
are written as flat paths and restored into nested schemas here:

- `a.b`: property `b` of object `a`. `a` must be declared before `a.b`.
- `a[]`: element of array `a`. `a[].b` declares an object element property;
  the element node is created implicitly when it is not declared.
- `a.<key>`: dynamic string-keyed map values of object `a`.
- `items: {type: ...}` on an array field is the same as declaring `a[]`.
- `root: true` on a single `params` / `result` entry describes the whole
  params or result value instead of a field inside it.

Standard entries use the upstream schema as their contract. A docs file for a
standard entry may only add Lynx-specific fields; redeclared upstream fields
must stay kind-compatible with upstream. Known shape differences from upstream
are registered as deviations in cdp_support.yaml and applied before docs load.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml



KINDS = frozenset(
    {"string", "integer", "number", "boolean", "object", "array", "any", "null"}
)
FIELD_KEYS = frozenset(
    {
        "name",
        "type",
        "$ref",
        "optional",
        "description",
        "default",
        "minimum",
        "maximum",
        "enum",
        "items",
        "typeName",
        "experimental",
        "deprecated",
        "root",
    }
)
ITEMS_KEYS = frozenset({"type", "$ref", "enum", "minimum", "maximum", "items"})
METHOD_DOC_KEYS = frozenset(
    {
        "summary",
        "parameters",
        "returns",
        "events",
        "notes",
        "examples",
        "experimental",
        "deprecated",
        "since",
        "legacy",
    }
)
EVENT_DOC_KEYS = frozenset(
    {"summary", "parameters", "notes", "examples", "experimental", "deprecated", "since"}
)
SINCE_RE = re.compile(r"[0-9]+\.[0-9]+")
EXAMPLE_KEYS = frozenset({"title", "request", "response", "event"})
REQUEST_KEYS = frozenset({"id", "method", "params", "sessionId"})
RESPONSE_KEYS = frozenset({"id", "result", "error", "sessionId"})
EVENT_MESSAGE_KEYS = frozenset({"method", "params", "sessionId"})
ERROR_KEYS = frozenset({"code", "message", "data"})
ROOT_NAMES = {"parameters": "params", "returns": "result"}
SUPPORT_DOMAIN_KEYS = frozenset(
    {"deviations", "cdpc", "methods", "events", "validation", "legacy", "golden"}
)
# Implemented inside the JS engines; Lynx only forwards their messages.
ENGINE_DOMAINS = frozenset({"Debugger", "Runtime", "HeapProfiler", "Profiler"})
CDPC_MODES = ("contract", "types", "dispatch")
ENTRY_SUPPORT_KEYS = frozenset({"cdpc", "reason"})
VALIDATION_MODES = frozenset({"legacy", "strict"})
LEGACY_DOMAIN_KEYS = frozenset({"agent", "order", "unknownMethodMessage", "migrated"})
LEGACY_METHOD_KEYS = frozenset({"validate", "thread", "errors", "defaults"})
LEGACY_ERROR_KEYS = frozenset({"field", "reason", "code", "message"})
LEGACY_VALIDATE = frozenset({"decode", "handler"})
LEGACY_THREADS = frozenset({"none", "tasm", "ui", "devtool"})
LEGACY_REASONS = frozenset({"missing", "type", "enum"})
LEGACY_ERROR_CODES = frozenset(
    {
        "ParseError",
        "InvalidRequest",
        "MethodNotFound",
        "InvalidParams",
        "InternalError",
        "ServerError",
    }
)
GOLDEN_KEYS = frozenset({"file", "allowedDiffs", "volatile"})
ALLOWED_DIFF_KEYS = frozenset({"case", "path", "from", "to", "reason", "expires"})
VOLATILE_KINDS = frozenset({"monotonic"})
TODO_TOKEN = "TODO(cdpc)"
DEVIATION_KEYS = frozenset({"target", "kind", "as", "reason"})
DEVIATION_KINDS = frozenset({"added", "omitted", "retyped"})
AS_KEYS = FIELD_KEYS - {"name", "description", "default", "root"}
DYNAMIC_KEY = "<key>"
INVALID_PARAMS_CODE = -32602
ENTRY_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*\.[A-Za-z][A-Za-z0-9_]*")
PROPERTY_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
TYPE_NAME_RE = re.compile(r"[A-Z][A-Za-z0-9]*")


class ContractError(Exception):
    pass


@dataclass
class Schema:
    """One value shape. `ref` and `kinds` are mutually exclusive."""

    kinds: frozenset[str] = frozenset()
    ref: Optional[str] = None
    enum: Optional[tuple[str, ...]] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    items: Optional["Schema"] = None
    # None means an object without declared properties (any object).
    properties: Optional[dict[str, "Property"]] = None
    additional: Optional["Schema"] = None
    type_name: Optional[str] = None


@dataclass
class Property:
    schema: Schema
    optional: bool = False
    experimental: bool = False
    deprecated: bool = False


@dataclass
class UpstreamEntry:
    parameters: Schema
    returns: Optional[Schema]


@dataclass
class Upstream:
    domains: set[str]
    commands: dict[tuple[str, str], UpstreamEntry]
    events: dict[tuple[str, str], UpstreamEntry]
    types: dict[str, Schema]


@dataclass
class ContractEntry:
    kind: str  # "method" or "event"
    domain: str
    name: str
    source: Path
    origin: str  # "standard" or "lynx-extension"
    parameters: Schema
    returns: Optional[Schema]
    # A root `params` entry marked optional lets requests omit params.
    params_optional: bool = False
    event_refs: list[str] = field(default_factory=list)
    experimental: bool = False
    deprecated: bool = False
    # Hand-maintained Lynx version that introduced the entry, such as "4.1".
    since: Optional[str] = None
    examples: list[dict] = field(default_factory=list)
    summary: str = ""
    # Standard entries only: the docs fields as written, before the upstream
    # overlay. Nested docs fields are not part of the contract; the upstream
    # diff report lists them.
    declared_parameters: Optional[Schema] = None
    declared_returns: Optional[Schema] = None

    @property
    def key(self) -> str:
        return f"{self.domain}.{self.name}"

    # Set only for methods. Ignored, with a warning, while the domain is strict.
    legacy: Optional["MethodLegacy"] = None
    # False for an upstream lookup without a local documentation file.
    documented: bool = True


@dataclass
class LegacyError:
    """One old error text that overrides the codec message for a field."""

    field: str
    reason: str
    code: str
    message: str


@dataclass
class MethodLegacy:
    """Where an old handler validated, and the texts it produced."""

    validate: str = "decode"  # decode | handler
    thread: str = "none"  # none | tasm | ui | devtool
    errors: list[LegacyError] = field(default_factory=list)
    defaults: dict[str, Any] = field(default_factory=dict)


@dataclass
class DomainLegacy:
    """Migration bookkeeping. Removed by `migrate <Domain> --apply` once every method is typed."""

    agent: Optional[str] = None
    order: list[str] = field(default_factory=list)
    unknown_method_message: Optional[str] = None
    migrated: list[str] = field(default_factory=list)


@dataclass
class AllowedDiff:
    case: str
    path: str
    from_text: str
    to_text: str
    reason: str
    expires: Optional[str] = None


@dataclass
class VolatileField:
    path: str
    kind: str


@dataclass
class GoldenSpec:
    file: Optional[str] = None
    allowed_diffs: list[AllowedDiff] = field(default_factory=list)
    volatile: list[VolatileField] = field(default_factory=list)


@dataclass
class Deviation:
    domain: str
    target: str
    kind: str
    as_spec: Optional[dict]
    reason: str


@dataclass
class ContractSet:
    methods: dict[tuple[str, str], ContractEntry]
    events: dict[tuple[str, str], ContractEntry]
    deviations: list[Deviation]
    errors: list[str]
    warnings: list[str]
    # Upstream schema with deviations applied; None only in hand-built sets.
    upstream: Optional[Upstream] = None
    # Defaults and per-entry overrides, independent of the supported inventory.
    domain_modes: dict[str, str] = field(default_factory=dict)
    explicit_domain_modes: set[str] = field(default_factory=set)
    entry_modes: dict[tuple[str, str, str], str] = field(default_factory=dict)
    # Explicitly selected entries; upstream selection is required for generation.
    selections: dict[str, tuple[list[str], list[str]]] = field(default_factory=dict)
    support_file: Optional[Path] = None
    # `validation` defaults to strict, so a dispatch domain that omits it (WhiteBoard)
    # keeps the current decode-before-dispatch behavior.
    validation: dict[str, str] = field(default_factory=dict)
    domain_legacy: dict[str, DomainLegacy] = field(default_factory=dict)
    domain_golden: dict[str, GoldenSpec] = field(default_factory=dict)

    def entry(self, kind: str, domain: str, name: str) -> Optional[ContractEntry]:
        """Resolve a wire contract, preferring local docs over effective upstream.

        Lookup does not select an entry for codegen or prove a source implements it.
        """
        docs = self.methods if kind == "method" else self.events
        if (domain, name) in docs:
            return docs[(domain, name)]
        upstream = (self.upstream.commands if kind == "method" else self.upstream.events) if self.upstream else {}
        shape = upstream.get((domain, name))
        if shape is None:
            return None
        return ContractEntry(
            kind=kind, domain=domain, name=name, source=self.support_file,
            origin="standard", parameters=shape.parameters, returns=shape.returns,
            documented=False,
        )

    def entries(
        self, domain: Optional[str] = None, *, kind: Optional[str] = None,
        modes: Optional[set[str]] = None,
    ) -> list[ContractEntry]:
        """Documented or explicitly selected entries, with current policy filters.

        Resolve on demand: migration plans and tests can change selections/modes.
        """
        result = []
        for candidate, docs, index in (("method", self.methods, 0), ("event", self.events, 1)):
            if kind is not None and kind != candidate:
                continue
            keys = set(docs) | {(owner, name) for owner, names in self.selections.items() for name in names[index]}
            for owner, name in sorted(keys):
                if domain is not None and domain != owner:
                    continue
                if modes is not None and entry_mode(self, candidate, owner, name) not in modes:
                    continue
                entry = self.entry(candidate, owner, name)
                if entry is not None:
                    result.append(entry)
        return result

    @property
    def generated_domains(self) -> set[str]:
        return {domain for domain, mode in self.domain_modes.items() if mode != "contract"} | {
            domain for (_, domain, _), mode in self.entry_modes.items() if mode != "contract"
        }

    @property
    def dispatch_domains(self) -> set[str]:
        return {domain for domain in self.generated_domains if any(
            entry.kind == "method" and entry_mode(self, "method", domain, entry.name) == "dispatch"
            for entry in self.entries(domain)
        )}


def entry_mode(contracts: ContractSet, kind: str, domain: str, name: str) -> str:
    """Resolve one entry. A domain's dispatch policy gives events types only."""

    default = contracts.domain_modes.get(domain, "contract")
    if kind == "event" and default == "dispatch":
        default = "types"
    return contracts.entry_modes.get((kind, domain, name), default)


def needs_manual_dispatch(contracts: ContractSet, domain: str) -> bool:
    """A mixed domain must keep an explicit owner for its manual routes."""

    return contracts.domain_modes.get(domain, "contract") != "dispatch" or any(
        entry.kind == "method" and entry_mode(contracts, "method", domain, entry.name) != "dispatch"
        for entry in contracts.entries(domain)
    )


def domain_validation(contracts: ContractSet, domain: str) -> str:
    return contracts.validation.get(domain, "strict")


class _LineDict(dict):
    line = 0


class _LineLoader(yaml.SafeLoader):
    pass


def _construct_line_map(loader, node):
    data = _LineDict()
    data.line = node.start_mark.line + 1
    yield data
    data.update(loader.construct_mapping(node))


_LineLoader.add_constructor("tag:yaml.org,2002:map", _construct_line_map)


def line_of(value: Any) -> int:
    return getattr(value, "line", 0)


# ---------------------------------------------------------------------------
# Upstream schema


def load_upstream(schema_path: Path) -> Upstream:
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read upstream CDP schema {schema_path}: {exc}")

    upstream = Upstream(domains=set(), commands={}, events={}, types={})
    for domain_entry in schema.get("domains", []):
        domain = domain_entry["domain"]
        upstream.domains.add(domain)
        for type_entry in domain_entry.get("types", []):
            upstream.types[f"{domain}.{type_entry['id']}"] = _upstream_schema(
                type_entry, domain
            )
        for command in domain_entry.get("commands", []):
            upstream.commands[(domain, command["name"])] = UpstreamEntry(
                parameters=_upstream_object(command.get("parameters", []), domain),
                returns=_upstream_object(command.get("returns", []), domain),
            )
        for event in domain_entry.get("events", []):
            upstream.events[(domain, event["name"])] = UpstreamEntry(
                parameters=_upstream_object(event.get("parameters", []), domain),
                returns=None,
            )
    return upstream


def _upstream_object(properties: list[dict], domain: str) -> Schema:
    return Schema(
        kinds=frozenset({"object"}),
        properties={
            prop["name"]: Property(
                schema=_upstream_schema(prop, domain),
                optional=bool(prop.get("optional", False)),
                experimental=bool(prop.get("experimental", False)),
                deprecated=bool(prop.get("deprecated", False)),
            )
            for prop in properties
        },
    )


def _upstream_schema(spec: dict, domain: str) -> Schema:
    if "$ref" in spec:
        ref = spec["$ref"]
        return Schema(ref=ref if "." in ref else f"{domain}.{ref}")
    kind = spec.get("type", "any")
    if kind == "object":
        if "properties" in spec:
            return _upstream_object(spec["properties"], domain)
        return Schema(kinds=frozenset({"object"}))
    if kind == "array":
        return Schema(
            kinds=frozenset({"array"}),
            items=_upstream_schema(spec.get("items", {"type": "any"}), domain),
        )
    enum = tuple(spec["enum"]) if "enum" in spec else None
    return Schema(kinds=frozenset({kind}), enum=enum)


# ---------------------------------------------------------------------------
# Docs loading


def load_contracts(
    custom_docs_dir: Path, support_file: Path, upstream: Upstream, display
) -> ContractSet:
    """Load and validate every docs file. `display` maps a Path to a report path.

    Deviations from `support_file` are applied to `upstream` in place, so later
    lookups of upstream entries see the effective Lynx shape.
    """

    contracts = ContractSet(
        methods={},
        events={},
        deviations=[],
        errors=[],
        warnings=[],
        upstream=upstream,
        support_file=support_file,
    )
    _load_support(support_file, upstream, contracts, display)
    for kind, section in (("method", "methods"), ("event", "events")):
        for doc_file in sorted(custom_docs_dir.glob(f"*/{section}/*.yaml")):
            domain = doc_file.parents[1].name
            name = doc_file.stem
            reporter = _Reporter(display(doc_file), contracts.errors, contracts.warnings)
            entry = _load_entry(doc_file, kind, domain, name, upstream, reporter)
            if entry is None:
                continue
            target = contracts.methods if kind == "method" else contracts.events
            target[(domain, name)] = entry

    documented = {entry.domain for entry in [*contracts.methods.values(), *contracts.events.values()]}
    for domain in sorted(contracts.generated_domains - documented - upstream.domains - ENGINE_DOMAINS):
        contracts.errors.append(
            f"{display(support_file)}: {domain}: cdpc generation needs docs under custom_cdp_docs/{domain}/"
        )
    for domain, selection in contracts.selections.items():
        for kind, docs, selected, known in (
            ("method", contracts.methods, selection[0], upstream.commands),
            ("event", contracts.events, selection[1], upstream.events),
        ):
            for name in selected:
                if (domain, name) not in docs and (domain, name) not in known:
                    contracts.errors.append(f"{display(support_file)}: {domain}: unknown {kind} `{name}`")
    for kind, domain, name in contracts.entry_modes:
        docs = contracts.methods if kind == "method" else contracts.events
        known = upstream.commands if kind == "method" else upstream.events
        if (domain, name) not in docs and (domain, name) not in known:
            continue  # Already reported through selections.
        if domain in ENGINE_DOMAINS and entry_mode(contracts, kind, domain, name) != "contract":
            contracts.errors.append(f"{display(support_file)}: {domain}: engine-forwarded domains stay out of codegen")
    for domain in contracts.domain_legacy:
        if contracts.errors:
            break
        if any(entry.kind == "method" and entry_mode(contracts, "method", domain, entry.name) != "dispatch"
               for entry in contracts.entries(domain)):
            contracts.errors.append(f"{display(support_file)}: {domain}: legacy adapter requires every method to use cdpc dispatch")
    validator = _Validator(upstream, contracts)
    for entry in [*contracts.methods.values(), *contracts.events.values()]:
        reporter = _Reporter(display(entry.source), contracts.errors)
        _check_event_refs(entry, upstream, contracts, reporter)
        _check_examples(entry, validator, reporter, contracts.warnings)
    _check_legacy_consistency(contracts, display)
    _scan_todos(support_file, custom_docs_dir, contracts.errors, display)
    return contracts


class _Reporter:
    def __init__(self, path: str, sink: list[str], warnings: Optional[list[str]] = None):
        self.path = path
        self.sink = sink
        self.warnings = warnings

    def error(self, line: int, message: str) -> None:
        location = f"{self.path}:{line}" if line else self.path
        self.sink.append(f"{location}: {message}")

    def contracts_warning(self, message: str) -> None:
        if self.warnings is not None:
            self.warnings.append(f"{self.path}: {message}")


def _load_support(support_file: Path, upstream: Upstream, contracts: ContractSet, display) -> None:
    if not support_file.exists():
        return
    reporter = _Reporter(display(support_file), contracts.errors)
    try:
        support = yaml.load(support_file.read_text(encoding="utf-8"), Loader=_LineLoader)
    except yaml.YAMLError as exc:
        reporter.error(0, f"invalid YAML: {exc}")
        return
    if support is None:
        return
    if not isinstance(support, dict):
        reporter.error(0, "support file must be a YAML mapping keyed by domain")
        return
    for domain, config in support.items():
        if not isinstance(config, dict):
            reporter.error(line_of(support), f"{domain}: must be a mapping")
            continue
        for key in sorted(set(config) - SUPPORT_DOMAIN_KEYS):
            reporter.error(line_of(config), f"{domain}: unknown key `{key}`")
        mode = config.get("cdpc", "contract")
        if not isinstance(mode, str) or mode not in CDPC_MODES:
            reporter.error(line_of(config), f"{domain}: cdpc must be one of {list(CDPC_MODES)}")
            mode = "contract"
        contracts.domain_modes[domain] = mode
        if "cdpc" in config:
            contracts.explicit_domain_modes.add(domain)
        if domain in ENGINE_DOMAINS and mode != "contract":
            reporter.error(line_of(config), f"{domain}: engine-forwarded domains stay out of codegen")
        if domain in upstream.domains and mode != "contract" and not any(key in config for key in ("methods", "events")):
            reporter.error(line_of(config), f"{domain}: standard generated domains must select implemented `methods` or `events`")
        selection = [
            _load_selection(domain, kind, config.get(kind + "s", []), mode, contracts, reporter)
            for kind in ("method", "event")
        ]
        if "methods" in config or "events" in config:
            contracts.selections[domain] = (selection[0], selection[1])
        has_dispatch = mode == "dispatch" or any(
            kind == "method" and owner == domain and selected == "dispatch"
            for (kind, owner, _), selected in contracts.entry_modes.items()
        )
        if has_dispatch:
            _load_domain_migration(domain, config, contracts, reporter)
        elif any(key in config for key in ("validation", "legacy")):
            reporter.error(line_of(config), f"{domain}: validation and legacy need cdpc dispatch methods")
        if "golden" in config:
            parsed_golden = _parse_golden(domain, config["golden"], reporter)
            if parsed_golden is not None:
                contracts.domain_golden[domain] = parsed_golden
        if "deviations" in config and domain not in upstream.domains:
            reporter.error(line_of(support), f"{domain}: deviations only apply to upstream domains")
            continue
        for item in config.get("deviations") or []:
            deviation = _apply_deviation(domain, item, upstream, reporter)
            if deviation is not None:
                contracts.deviations.append(deviation)


def _load_selection(
    domain: str, kind: str, entries: Any, mode: str,
    contracts: ContractSet, reporter: _Reporter,
) -> list[str]:
    """Parse an explicit entry selection and resolve overrides in one place."""

    if isinstance(entries, list) and all(isinstance(name, str) and name for name in entries):
        if len(set(entries)) != len(entries):
            reporter.error(line_of(entries), f"{domain}: duplicate names in `{kind}s`")
        return list(entries)
    if not isinstance(entries, dict):
        reporter.error(line_of(entries), f"{domain}: `{kind}s` must be a list of names or a mapping")
        return []
    names = []
    inherited = "types" if kind == "event" and mode == "dispatch" else mode
    allowed = CDPC_MODES[:-1] if kind == "event" else CDPC_MODES
    for name, settings in entries.items():
        if not isinstance(name, str) or not name:
            reporter.error(line_of(entries), f"{domain}: `{kind}s` needs non-empty entry names")
            continue
        names.append(name)
        if not isinstance(settings, dict):
            reporter.error(line_of(entries), f"{domain}.{name}: settings must be a mapping")
            continue
        for unknown in sorted(set(settings) - ENTRY_SUPPORT_KEYS):
            reporter.error(line_of(settings), f"{domain}.{name}: unknown key `{unknown}`")
        override = settings.get("cdpc", inherited)
        if not isinstance(override, str) or override not in allowed:
            reporter.error(line_of(settings), f"{domain}.{name}: cdpc must be one of {list(allowed)}")
            continue
        reason = settings.get("reason")
        valid_reason = isinstance(reason, str) and bool(reason.strip())
        if reason is not None and not valid_reason:
            reporter.error(line_of(settings), f"{domain}.{name}: reason must be a non-empty string")
        elif CDPC_MODES.index(override) < CDPC_MODES.index(inherited) and not valid_reason:
            reporter.error(line_of(settings), f"{domain}.{name}: lowering cdpc needs a non-empty `reason`")
        contracts.entry_modes[(kind, domain, name)] = override
    return names


def _load_domain_migration(
    domain: str, config: dict, contracts: ContractSet, reporter: _Reporter
) -> None:
    """Compatibility settings for generated dispatch. Default validation is strict."""

    mode = config.get("validation", "strict")
    if mode not in VALIDATION_MODES:
        reporter.error(
            line_of(config),
            f"{domain}: validation must be one of {sorted(VALIDATION_MODES)}",
        )
    else:
        contracts.validation[domain] = mode
    if "legacy" in config:
        parsed = _parse_domain_legacy(domain, config["legacy"], reporter)
        if parsed is not None:
            contracts.domain_legacy[domain] = parsed


def _parse_domain_legacy(domain: str, value: Any, reporter: _Reporter) -> Optional[DomainLegacy]:
    if not isinstance(value, dict):
        reporter.error(line_of(value) if isinstance(value, dict) else 0, f"{domain}: `legacy` must be a mapping")
        return None
    for key in sorted(set(value) - LEGACY_DOMAIN_KEYS):
        reporter.error(line_of(value), f"{domain}: unknown legacy key `{key}`")
    agent = value.get("agent")
    if agent is not None and not isinstance(agent, str):
        reporter.error(line_of(value), f"{domain}: legacy.agent must be a path")
        agent = None
    order = _name_list(value.get("order"), f"{domain}: legacy.order", reporter, value)
    migrated = _name_list(value.get("migrated", []), f"{domain}: legacy.migrated", reporter, value)
    message = value.get("unknownMethodMessage")
    if message is not None and not isinstance(message, str):
        reporter.error(line_of(value), f"{domain}: legacy.unknownMethodMessage must be a string")
        message = None
    if order is None or migrated is None:
        return None
    return DomainLegacy(
        agent=agent,
        order=order,
        unknown_method_message=message,
        migrated=migrated,
    )


def _name_list(value: Any, label: str, reporter: _Reporter, owner: Any) -> Optional[list[str]]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        reporter.error(line_of(owner), f"{label} must be a list of method names")
        return None
    return list(value)


def _parse_golden(domain: str, value: Any, reporter: _Reporter) -> Optional[GoldenSpec]:
    if not isinstance(value, dict):
        reporter.error(0, f"{domain}: `golden` must be a mapping")
        return None
    for key in sorted(set(value) - GOLDEN_KEYS):
        reporter.error(line_of(value), f"{domain}: unknown golden key `{key}`")
    path = value.get("file")
    if path is not None and not isinstance(path, str):
        reporter.error(line_of(value), f"{domain}: golden.file must be a path")
        path = None
    diffs: list[AllowedDiff] = []
    for item in value.get("allowedDiffs") or []:
        parsed = _parse_allowed_diff(domain, item, reporter)
        if parsed is not None:
            diffs.append(parsed)
    volatile: list[VolatileField] = []
    for item in value.get("volatile") or []:
        parsed_field = _parse_volatile(domain, item, reporter)
        if parsed_field is not None:
            volatile.append(parsed_field)
    return GoldenSpec(file=path, allowed_diffs=diffs, volatile=volatile)


def _parse_allowed_diff(domain: str, item: Any, reporter: _Reporter) -> Optional[AllowedDiff]:
    if not isinstance(item, dict):
        reporter.error(0, f"{domain}: golden.allowedDiffs entries must be mappings")
        return None
    for key in sorted(set(item) - ALLOWED_DIFF_KEYS):
        reporter.error(line_of(item), f"{domain}: unknown allowedDiffs key `{key}`")
    missing = [key for key in ("case", "path", "from", "to", "reason") if not isinstance(item.get(key), str) or not item.get(key)]
    if "reason" not in item or not isinstance(item.get("reason"), str) or not str(item.get("reason")).strip():
        reporter.error(line_of(item), f"{domain}: golden.allowedDiffs.reason is required")
    if missing:
        reporter.error(
            line_of(item),
            f"{domain}: golden.allowedDiffs entry needs case, path, from, to and reason",
        )
        return None
    expires = item.get("expires")
    if expires is not None and (not isinstance(expires, str) or not SINCE_RE.fullmatch(expires)):
        reporter.error(line_of(item), f"{domain}: golden.allowedDiffs.expires must be a quoted major.minor")
        expires = None
    return AllowedDiff(
        case=item["case"],
        path=item["path"],
        from_text=item["from"],
        to_text=item["to"],
        reason=item["reason"].strip(),
        expires=expires,
    )


def _parse_volatile(domain: str, item: Any, reporter: _Reporter) -> Optional[VolatileField]:
    if not isinstance(item, dict):
        reporter.error(0, f"{domain}: golden.volatile entries must be mappings")
        return None
    path = item.get("path")
    kind = item.get("kind")
    if not isinstance(path, str) or not path:
        reporter.error(line_of(item), f"{domain}: golden.volatile.path is required")
        return None
    if kind not in VOLATILE_KINDS:
        reporter.error(
            line_of(item),
            f"{domain}: golden.volatile.kind must be one of {sorted(VOLATILE_KINDS)}",
        )
        return None
    return VolatileField(path=path, kind=kind)


def _parse_method_legacy(value: Any, reporter: _Reporter) -> Optional[MethodLegacy]:
    if not isinstance(value, dict):
        reporter.error(0, "`legacy` must be a mapping")
        return None
    for key in sorted(set(value) - LEGACY_METHOD_KEYS):
        reporter.error(line_of(value), f"unknown legacy key `{key}`")
    validate = value.get("validate", "decode")
    thread = value.get("thread", "none")
    if validate not in LEGACY_VALIDATE:
        reporter.error(line_of(value), f"legacy.validate must be one of {sorted(LEGACY_VALIDATE)}")
        validate = "decode"
    if thread not in LEGACY_THREADS:
        reporter.error(line_of(value), f"legacy.thread must be one of {sorted(LEGACY_THREADS)}")
        thread = "none"
    errors: list[LegacyError] = []
    for item in value.get("errors") or []:
        parsed = _parse_legacy_error(item, reporter)
        if parsed is not None:
            errors.append(parsed)
    defaults = value.get("defaults", {})
    if not isinstance(defaults, dict):
        reporter.error(line_of(value), "legacy.defaults must be a mapping of field names to values")
        defaults = {}
    return MethodLegacy(validate=validate, thread=thread, errors=errors, defaults=dict(defaults))


def _parse_legacy_error(item: Any, reporter: _Reporter) -> Optional[LegacyError]:
    if not isinstance(item, dict):
        reporter.error(0, "legacy.errors entries must be mappings")
        return None
    for key in sorted(set(item) - LEGACY_ERROR_KEYS):
        reporter.error(line_of(item), f"unknown legacy.errors key `{key}`")
    field_name = item.get("field")
    reason = item.get("reason")
    code = item.get("code")
    message = item.get("message")
    if not isinstance(field_name, str):
        reporter.error(line_of(item), "legacy.errors.field must be a string (empty for the params value)")
        return None
    if reason not in LEGACY_REASONS:
        reporter.error(line_of(item), f"legacy.errors.reason must be one of {sorted(LEGACY_REASONS)}")
        return None
    if code not in LEGACY_ERROR_CODES:
        reporter.error(line_of(item), f"legacy.errors.code must be one of {sorted(LEGACY_ERROR_CODES)}")
        return None
    if not isinstance(message, str) or not message:
        reporter.error(line_of(item), "legacy.errors.message must be a non-empty string")
        return None
    return LegacyError(field=field_name, reason=reason, code=code, message=message)


def _check_legacy_consistency(contracts: ContractSet, display) -> None:
    """order/migrated name the domain's methods; strict domains only warn about leftover legacy."""

    for (domain, name), entry in sorted(contracts.methods.items()):
        if entry.legacy is None:
            continue
        if domain_validation(contracts, domain) == "strict":
            contracts.warnings.append(
                f"{display(entry.source)}: legacy block is ignored while validation is strict; "
                "`migrate <Domain> --apply` removes it"
            )
            continue
        for field_name in entry.legacy.defaults:
            prop = (entry.parameters.properties or {}).get(field_name)
            if prop is None or not prop.optional:
                contracts.errors.append(
                    f"{display(entry.source)}: legacy.defaults.{field_name} "
                    "must be an optional parameter"
                )
    for domain, legacy in sorted(contracts.domain_legacy.items()):
        names = [
            entry.name
            for entry in contract_methods(contracts, domain)
        ]
        known = set(names)
        if domain_validation(contracts, domain) != "legacy":
            contracts.warnings.append(
                f"{display(contracts.support_file) if contracts.support_file else domain}: "
                f"{domain}: legacy block is ignored while validation is strict"
            )
            continue
        if not legacy.order:
            contracts.errors.append(
                f"{display(contracts.support_file) if contracts.support_file else domain}: "
                f"{domain}: legacy.order must list every method in the old registration order"
            )
        elif sorted(legacy.order) != sorted(names) or len(legacy.order) != len(set(legacy.order)):
            contracts.errors.append(
                f"{display(contracts.support_file) if contracts.support_file else domain}: "
                f"{domain}: legacy.order must list each method once, got {legacy.order} "
                f"for {sorted(names)}"
            )
        unknown = [name for name in legacy.migrated if name not in known]
        if unknown:
            contracts.errors.append(
                f"{display(contracts.support_file) if contracts.support_file else domain}: "
                f"{domain}: legacy.migrated names unknown methods {unknown}"
            )
        if len(legacy.migrated) != len(set(legacy.migrated)):
            contracts.errors.append(
                f"{display(contracts.support_file) if contracts.support_file else domain}: "
                f"{domain}: legacy.migrated lists a method more than once"
            )
    for domain, golden in sorted(contracts.domain_golden.items()):
        if domain_validation(contracts, domain) == "legacy" and not golden.file:
            contracts.warnings.append(
                f"{display(contracts.support_file) if contracts.support_file else domain}: "
                f"{domain}: validation is legacy but golden.file is not set"
            )


def contract_methods(contracts: ContractSet, domain: str) -> list[ContractEntry]:
    return sorted(
        (entry for entry in contracts.methods.values() if entry.domain == domain),
        key=lambda entry: entry.name,
    )


def _scan_todos(support_file: Path, docs_dir: Path, errors: list[str], display) -> None:
    """`TODO(cdpc)` is a placeholder inference could not fill. check rejects it."""

    paths = []
    if support_file.exists():
        paths.append(support_file)
    paths.extend(sorted(docs_dir.glob("*/*/*.yaml")))
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        shown = display(path)
        for number, line in enumerate(lines, 1):
            if TODO_TOKEN in line:
                errors.append(f"{shown}:{number}: TODO(cdpc) must be resolved")


def _apply_deviation(domain: str, item: Any, upstream: Upstream, reporter: _Reporter):
    line = line_of(item)
    if not isinstance(item, dict):
        reporter.error(line, f"{domain}: deviation must be a mapping")
        return None
    for key in sorted(set(item) - DEVIATION_KEYS):
        reporter.error(line, f"unknown deviation key `{key}`")
    target, kind, as_spec, reason = (
        item.get("target"),
        item.get("kind"),
        item.get("as"),
        item.get("reason"),
    )
    if kind not in DEVIATION_KINDS:
        reporter.error(line, f"deviation kind must be one of {sorted(DEVIATION_KINDS)}")
        return None
    if not isinstance(reason, str) or not reason.strip():
        reporter.error(line, "deviation needs a non-empty `reason`")
        return None
    if (kind == "omitted") != (as_spec is None):
        reporter.error(line, "`as` is required for added / retyped and forbidden for omitted")
        return None

    parts = target.split(".", 2) if isinstance(target, str) else []
    if len(parts) != 3 or parts[1] not in ("parameters", "returns"):
        reporter.error(line, f"target must be `<entry>.parameters|returns.<field>`, got {target!r}")
        return None
    entry_name, section, field_path = parts
    candidates = []
    if (domain, entry_name) in upstream.commands:
        candidates.append(upstream.commands[(domain, entry_name)])
    if section == "parameters" and (domain, entry_name) in upstream.events:
        candidates.append(upstream.events[(domain, entry_name)])
    if len(candidates) != 1:
        reporter.error(line, f"{domain}.{entry_name} must name exactly one upstream command or event")
        return None
    section_schema = getattr(candidates[0], section)
    tokens = _parse_path(field_path)
    if section_schema is None or tokens is None or any(token[0] != "prop" for token in tokens):
        reporter.error(line, f"invalid deviation field path {field_path!r}")
        return None

    parent = section_schema
    for token in tokens[:-1]:
        prop = (parent.properties or {}).get(token[1])
        if prop is None or prop.schema.ref is not None or prop.schema.properties is None:
            reporter.error(line, f"{target}: parent must be an inline upstream object")
            return None
        parent = prop.schema
    name = tokens[-1][1]
    exists = name in (parent.properties or {})
    if (kind == "added") == exists:
        reporter.error(line, f"{target}: {kind} requires the field to {'not ' if kind == 'added' else ''}exist upstream")
        return None

    if kind == "omitted":
        del parent.properties[name]
    else:
        if not isinstance(as_spec, dict):
            reporter.error(line, "`as` must be a mapping")
            return None
        for key in sorted(set(as_spec) - AS_KEYS):
            reporter.error(line, f"unknown `as` key `{key}`")
        schema = _field_schema(as_spec, line, reporter)
        if schema is None:
            return None
        if parent.properties is None:
            parent.properties = {}
        optional = as_spec.get("optional")
        if kind == "retyped" and optional is None:
            optional = parent.properties[name].optional
        parent.properties[name] = Property(schema=schema, optional=bool(optional))
    return Deviation(domain=domain, target=target, kind=kind, as_spec=as_spec, reason=reason)


def _load_entry(
    doc_file: Path,
    kind: str,
    domain: str,
    name: str,
    upstream: Upstream,
    reporter: _Reporter,
) -> Optional[ContractEntry]:
    try:
        doc = yaml.load(doc_file.read_text(encoding="utf-8"), Loader=_LineLoader)
    except yaml.YAMLError as exc:
        reporter.error(0, f"invalid YAML: {exc}")
        return None
    if not isinstance(doc, dict):
        reporter.error(0, "docs file must be a YAML mapping")
        return None

    allowed = METHOD_DOC_KEYS if kind == "method" else EVENT_DOC_KEYS
    for key in sorted(set(doc) - allowed):
        reporter.error(line_of(doc), f"unknown top-level key `{key}`")
    summary = doc.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        reporter.error(line_of(doc), "`summary` must be a non-empty string")
    for key in ("notes", "examples"):
        if key in doc and not isinstance(doc[key], list):
            reporter.error(line_of(doc), f"`{key}` must be a list")
    experimental = _optional_bool(doc, "experimental", reporter)
    deprecated = _optional_bool(doc, "deprecated", reporter)
    since = doc.get("since")
    if since is not None and (not isinstance(since, str) or not SINCE_RE.fullmatch(since)):
        reporter.error(
            line_of(doc),
            f"`since` must be a quoted `major.minor` string such as '4.1', got {since!r}",
        )
        since = None

    upstream_entry = (upstream.commands if kind == "method" else upstream.events).get(
        (domain, name)
    )
    parameters, params_root, params_optional = _build_fields(
        doc.get("parameters"), "parameters", reporter
    )
    returns, returns_root = None, False
    if kind == "method":
        returns, returns_root, _ = _build_fields(doc.get("returns"), "returns", reporter)
    if params_root and kind == "method" and "object" not in parameters.kinds:
        reporter.contracts_warning(
            "root params is not an object; DispatchCDPMessage rejects non-object "
            "params with InvalidRequest, so this shape cannot reach the handler"
        )
    if upstream_entry is not None and (params_root or returns_root):
        reporter.error(line_of(doc), "standard entries cannot use `root: true`")
        upstream_entry = None
    declared_parameters, declared_returns = parameters, returns
    if upstream_entry is not None:
        parameters = _overlay_upstream(
            upstream_entry.parameters, parameters, "parameters", upstream, reporter
        )
        if kind == "method":
            returns = _overlay_upstream(
                upstream_entry.returns, returns, "returns", upstream, reporter
            )

    event_refs: list[str] = []
    for index, ref in enumerate(doc.get("events") or []):
        ref_name = ref.get("name") if isinstance(ref, dict) else ref
        if not isinstance(ref_name, str) or not ENTRY_NAME_RE.fullmatch(ref_name):
            reporter.error(
                line_of(ref) or line_of(doc),
                f"events[{index}] must be `Domain.event` or a mapping with `name`",
            )
            continue
        if isinstance(ref, dict):
            for key in sorted(set(ref) - {"name", "description"}):
                reporter.error(line_of(ref), f"unknown event reference key `{key}`")
        event_refs.append(ref_name)

    examples = [item for item in doc.get("examples") or [] if isinstance(item, dict)]
    method_legacy = None
    if kind == "method" and "legacy" in doc:
        method_legacy = _parse_method_legacy(doc["legacy"], reporter)
    return ContractEntry(
        kind=kind,
        domain=domain,
        name=name,
        source=doc_file,
        origin="standard" if upstream_entry is not None else "lynx-extension",
        parameters=parameters,
        returns=returns,
        params_optional=params_optional,
        event_refs=event_refs,
        experimental=experimental,
        deprecated=deprecated,
        since=since,
        examples=examples,
        summary=" ".join(summary.split()) if isinstance(summary, str) else "",
        declared_parameters=declared_parameters if upstream_entry is not None else None,
        declared_returns=declared_returns if upstream_entry is not None else None,
        legacy=method_legacy,
    )


def _optional_bool(mapping: dict, key: str, reporter: _Reporter) -> bool:
    value = mapping.get(key, False)
    if not isinstance(value, bool):
        reporter.error(line_of(mapping), f"`{key}` must be a boolean")
        return False
    return value


def _overlay_upstream(
    upstream_schema: Schema,
    doc_schema: Schema,
    section: str,
    upstream: Upstream,
    reporter: _Reporter,
) -> Schema:
    """Use upstream as the contract and add doc-only fields as Lynx additions."""

    properties = dict(upstream_schema.properties or {})
    for name, prop in (doc_schema.properties or {}).items():
        upstream_prop = properties.get(name)
        if upstream_prop is None:
            properties[name] = prop
            continue
        upstream_kinds = _resolved_kinds(upstream_prop.schema, upstream)
        doc_kinds = _resolved_kinds(prop.schema, upstream)
        if "any" not in upstream_kinds and not doc_kinds <= _widen(upstream_kinds):
            reporter.error(
                0,
                f"{section}.{name} redeclares upstream field as "
                f"{'|'.join(sorted(doc_kinds))}, upstream is "
                f"{'|'.join(sorted(upstream_kinds))}; register a deviation instead",
            )
    return Schema(kinds=frozenset({"object"}), properties=properties)


def _resolved_kinds(schema: Schema, upstream: Upstream) -> frozenset[str]:
    seen = set()
    while schema.ref is not None and schema.ref not in seen:
        seen.add(schema.ref)
        target = upstream.types.get(schema.ref)
        if target is None:
            return frozenset({"any"})
        schema = target
    return schema.kinds


def _widen(kinds: frozenset[str]) -> frozenset[str]:
    return kinds | {"integer"} if "number" in kinds else kinds


# ---------------------------------------------------------------------------
# Flat docs fields -> nested schema


def _build_fields(fields: Any, section: str, reporter: _Reporter) -> tuple[Schema, bool, bool]:
    """Return (schema, is_root_entry, root_optional) for a parameters/returns list."""

    root = Schema(kinds=frozenset({"object"}), properties={})
    if fields is None:
        return root, False, False
    if not isinstance(fields, list):
        reporter.error(0, f"`{section}` must be a list")
        return root, False, False

    nodes: dict[tuple, Schema] = {(): root}
    is_root = False
    root_optional = False
    defaults: list[tuple[int, str, Any, Schema]] = []
    for index, item in enumerate(fields):
        line = line_of(item)
        if not isinstance(item, dict):
            reporter.error(0, f"{section}[{index}] must be a mapping")
            continue
        for key in sorted(set(item) - FIELD_KEYS):
            reporter.error(line, f"unknown field key `{key}`")
        path = item.get("name")
        tokens = _parse_path(path)
        if tokens is None:
            reporter.error(line, f"invalid field path {path!r}")
            continue
        if tokens in nodes:
            reporter.error(line, f"duplicate field path `{path}`")
            continue
        schema = _field_schema(item, line, reporter)
        if schema is None:
            continue
        optional = _optional_bool(item, "optional", reporter)
        if _optional_bool(item, "root", reporter):
            if index != 0 or path != ROOT_NAMES[section]:
                reporter.error(
                    line,
                    f"`root: true` must be on the first {section} entry named "
                    f"`{ROOT_NAMES[section]}`",
                )
                continue
            is_root = True
            root_optional = optional
            root = schema
            nodes = {tokens: schema}
            continue
        if is_root and tokens[0] != ("prop", ROOT_NAMES[section]):
            reporter.error(line, f"`{path}` must be nested under the root `{ROOT_NAMES[section]}` entry")
            continue
        last = tokens[-1]
        parent = _resolve_parent(nodes, tokens[:-1], path, line, reporter)
        if parent is None:
            continue
        if last[0] == "prop":
            if "object" not in parent.kinds:
                reporter.error(line, f"parent of `{path}` must include type object")
                continue
            if parent.properties is None:
                parent.properties = {}
            parent.properties[last[1]] = Property(
                schema=schema,
                optional=optional,
                experimental=_optional_bool(item, "experimental", reporter),
                deprecated=_optional_bool(item, "deprecated", reporter),
            )
        else:
            if optional:
                reporter.error(line, f"`{path}` is an element or map value; it cannot be optional")
            if not _attach_child(parent, last[0], schema, path, line, reporter):
                continue
        nodes[tokens] = schema
        if "default" in item:
            defaults.append((line, path, item["default"], schema))

    for line, path, value, schema in defaults:
        for problem in _Validator(None, None).check(value, schema, path, strict=True):
            reporter.error(line, f"default does not match field: {problem}")
    return root, is_root, root_optional


def _parse_path(path: Any) -> Optional[tuple]:
    if not isinstance(path, str) or not path:
        return None
    tokens: list[tuple] = []
    for segment in path.split("."):
        if segment == DYNAMIC_KEY:
            tokens.append(("key",))
            continue
        base = segment
        item_depth = 0
        while base.endswith("[]"):
            base = base[:-2]
            item_depth += 1
        if not PROPERTY_NAME_RE.fullmatch(base):
            return None
        tokens.append(("prop", base))
        tokens.extend(("item",) for _ in range(item_depth))
    if not tokens or tokens[0][0] != "prop":
        return None
    return tuple(tokens)


def _resolve_parent(
    nodes: dict[tuple, Schema], tokens: tuple, path: str, line: int, reporter: _Reporter
) -> Optional[Schema]:
    if tokens in nodes:
        return nodes[tokens]
    if not tokens or tokens[-1][0] == "prop":
        reporter.error(line, f"parent field of `{path}` must be declared first")
        return None
    # Element and map value nodes are created implicitly as objects.
    grandparent = _resolve_parent(nodes, tokens[:-1], path, line, reporter)
    if grandparent is None:
        return None
    implicit = Schema(kinds=frozenset({"object"}), properties={})
    if not _attach_child(grandparent, tokens[-1][0], implicit, path, line, reporter):
        return None
    nodes[tokens] = implicit
    return implicit


def _attach_child(
    parent: Schema, kind: str, schema: Schema, path: str, line: int, reporter: _Reporter
) -> bool:
    if kind == "item":
        if "array" not in parent.kinds:
            reporter.error(line, f"`{path}` uses [] but its parent is not an array")
            return False
        if parent.items is not None:
            reporter.error(line, f"array element of `{path}` is declared twice")
            return False
        parent.items = schema
        return True
    if "object" not in parent.kinds:
        reporter.error(line, f"`{path}` uses <key> but its parent is not an object")
        return False
    if parent.additional is not None:
        reporter.error(line, f"map value of `{path}` is declared twice")
        return False
    parent.additional = schema
    return True


def _field_schema(item: dict, line: int, reporter: _Reporter) -> Optional[Schema]:
    has_type = "type" in item
    has_ref = "$ref" in item
    if has_type == has_ref:
        reporter.error(line, "field must declare exactly one of `type` or `$ref`")
        return None
    if has_ref:
        ref = item["$ref"]
        if not isinstance(ref, str) or not ENTRY_NAME_RE.fullmatch(ref):
            reporter.error(line, f"`$ref` must be `Domain.Type`, got {ref!r}")
            return None
        for key in ("enum", "minimum", "maximum", "items"):
            if key in item:
                reporter.error(line, f"`{key}` cannot be combined with `$ref`")
        return Schema(ref=ref, type_name=_type_name(item, line, reporter))

    raw_type = item["type"]
    kinds = frozenset(raw_type.split("|")) if isinstance(raw_type, str) else frozenset()
    if not kinds or not kinds <= KINDS:
        reporter.error(line, f"unknown type {raw_type!r}")
        return None
    if "any" in kinds and len(kinds) > 1:
        reporter.error(line, "`any` cannot be part of a union")
        return None

    schema = Schema(kinds=kinds, type_name=_type_name(item, line, reporter))
    if "enum" in item:
        values = item["enum"]
        if "string" not in kinds or not isinstance(values, list) or not values or not all(
            isinstance(value, str) for value in values
        ):
            reporter.error(line, "`enum` must be a non-empty string list on a string field")
        else:
            schema.enum = tuple(values)
    for key in ("minimum", "maximum"):
        if key in item:
            value = item[key]
            if not ({"integer", "number"} & kinds) or isinstance(value, bool) or not isinstance(
                value, (int, float)
            ):
                reporter.error(line, f"`{key}` must be a number on a numeric field")
            else:
                setattr(schema, key, value)
    if "items" in item:
        items = item["items"]
        if "array" not in kinds:
            reporter.error(line, "`items` requires an array field")
        elif not isinstance(items, dict):
            reporter.error(line, "`items` must be a mapping")
        else:
            for key in sorted(set(items) - ITEMS_KEYS):
                reporter.error(line, f"unknown items key `{key}`")
            schema.items = _field_schema(items, line, reporter)
    return schema


def _type_name(item: dict, line: int, reporter: _Reporter) -> Optional[str]:
    value = item.get("typeName")
    if value is None:
        return None
    if not isinstance(value, str) or not TYPE_NAME_RE.fullmatch(value):
        reporter.error(line, f"`typeName` must be an UpperCamelCase identifier, got {value!r}")
        return None
    return value


# ---------------------------------------------------------------------------
# Value validation


class _Validator:
    def __init__(self, upstream: Optional[Upstream], contracts: Optional[ContractSet]):
        self.upstream = upstream
        self.contracts = contracts

    def event_schema(self, name: str) -> Optional[Schema]:
        domain, event = name.split(".", 1)
        if self.contracts is not None:
            entry = self.contracts.entry("event", domain, event)
            if entry is not None:
                return entry.parameters
        if self.upstream is not None and (domain, event) in self.upstream.events:
            return self.upstream.events[(domain, event)].parameters
        return None

    def check(self, value: Any, schema: Schema, path: str, strict: bool) -> list[str]:
        problems: list[str] = []
        self._check(value, schema, path, strict, problems)
        return problems

    def _check(self, value, schema, path, strict, problems) -> None:
        if schema.ref is not None:
            target = self.upstream.types.get(schema.ref) if self.upstream else None
            if target is None:
                problems.append(f"{path}: unresolved type reference {schema.ref}")
                return
            self._check(value, target, path, strict, problems)
            return

        kinds = schema.kinds
        if "any" in kinds:
            return
        kind = _value_kind(value)
        if kind not in kinds and not (kind == "integer" and "number" in kinds):
            problems.append(f"{path}: expected {'|'.join(sorted(kinds))}, got {kind}")
            return

        if kind == "string" and schema.enum is not None and value not in schema.enum:
            problems.append(f"{path}: {value!r} is not one of {list(schema.enum)}")
        elif kind in ("integer", "number"):
            if schema.minimum is not None and value < schema.minimum:
                problems.append(f"{path}: {value} is below minimum {schema.minimum}")
            if schema.maximum is not None and value > schema.maximum:
                problems.append(f"{path}: {value} is above maximum {schema.maximum}")
        elif kind == "array" and schema.items is not None:
            for index, element in enumerate(value):
                self._check(element, schema.items, f"{path}[{index}]", strict, problems)
        elif kind == "object":
            self._check_object(value, schema, path, strict, problems)

    def _check_object(self, value: dict, schema: Schema, path, strict, problems) -> None:
        properties = schema.properties
        if properties is None and schema.additional is None:
            return
        for name, prop in (properties or {}).items():
            if name not in value:
                if not prop.optional:
                    problems.append(f"{path}.{name}: missing required field")
                continue
            self._check(value[name], prop.schema, f"{path}.{name}", strict, problems)
        for name in value:
            if properties and name in properties:
                continue
            if schema.additional is not None:
                self._check(value[name], schema.additional, f"{path}.{name}", strict, problems)
            elif strict:
                problems.append(f"{path}.{name}: unknown field")


def _value_kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


# ---------------------------------------------------------------------------
# Cross-entry checks


def _check_event_refs(
    entry: ContractEntry, upstream: Upstream, contracts: ContractSet, reporter: _Reporter
) -> None:
    for ref in entry.event_refs:
        domain, event = ref.split(".", 1)
        if (domain, event) in contracts.events or (domain, event) in upstream.events:
            continue
        contracts.warnings.append(
            f"{reporter.path}: event {ref} has no contract; "
            f"add custom_cdp_docs/{domain}/events/{event}.yaml"
        )


def _check_examples(
    entry: ContractEntry, validator: _Validator, reporter: _Reporter, warnings: list[str]
) -> None:
    for index, example in enumerate(entry.examples):
        line = line_of(example)
        label = f"examples[{index}]"
        for key in sorted(set(example) - EXAMPLE_KEYS):
            reporter.error(line, f"{label}: unknown key `{key}`")
        if not isinstance(example.get("title"), str):
            reporter.error(line, f"{label}: `title` must be a string")

        request = example.get("request")
        response = example.get("response")
        event = example.get("event")
        if isinstance(response, dict) and "method" in response and "id" not in response:
            # Legacy event examples are written as a response without an id.
            if event is not None:
                reporter.error(line, f"{label}: has both `event` and an event-shaped `response`")
            event, response = response, None

        error_code = None
        if response is not None:
            error_code = _check_response(entry, response, request, label, line, validator, reporter)
        if request is not None:
            _check_request(entry, request, error_code, label, line, validator, reporter)
        if event is not None:
            _check_event_message(entry, event, label, line, validator, reporter, warnings)
        if request is None and response is None and event is None:
            reporter.error(line, f"{label}: needs a request, response, or event")


def _check_request(entry, request, error_code, label, line, validator, reporter) -> None:
    where = f"{label}.request"
    if not isinstance(request, dict):
        reporter.error(line, f"{where}: must be a mapping")
        return
    for key in sorted(set(request) - REQUEST_KEYS):
        reporter.error(line_of(request), f"{where}: unknown key `{key}`")
    if not isinstance(request.get("id"), int) or isinstance(request.get("id"), bool):
        reporter.error(line_of(request), f"{where}.id: must be an integer")
    method = request.get("method")
    if entry.kind == "method" and method != entry.key:
        reporter.error(line_of(request), f"{where}.method: expected {entry.key}, got {method!r}")
        return
    if entry.kind == "event":
        # A triggering request on an event doc belongs to another entry.
        return
    params = request.get("params")
    if params is None:
        if entry.params_optional:
            return
        params = {}
    problems = validator.check(params, entry.parameters, f"{where}.params", strict=True)
    if problems and error_code != INVALID_PARAMS_CODE:
        for problem in problems:
            reporter.error(line_of(request), problem)


def _check_response(entry, response, request, label, line, validator, reporter) -> Optional[int]:
    where = f"{label}.response"
    if not isinstance(response, dict):
        reporter.error(line, f"{where}: must be a mapping")
        return None
    for key in sorted(set(response) - RESPONSE_KEYS):
        reporter.error(line_of(response), f"{where}: unknown key `{key}`")
    if isinstance(request, dict) and "id" in response and response["id"] != request.get("id"):
        reporter.error(line_of(response), f"{where}.id: does not match request id")
    if ("result" in response) == ("error" in response):
        reporter.error(line_of(response), f"{where}: must contain exactly one of result or error")
        return None
    if "error" in response:
        error = response["error"]
        if not isinstance(error, dict):
            reporter.error(line_of(response), f"{where}.error: must be a mapping")
            return None
        for key in sorted(set(error) - ERROR_KEYS):
            reporter.error(line_of(error), f"{where}.error: unknown key `{key}`")
        code = error.get("code")
        if not isinstance(code, int) or isinstance(code, bool):
            reporter.error(line_of(error), f"{where}.error.code: must be an integer")
            code = None
        if not isinstance(error.get("message"), str):
            reporter.error(line_of(error), f"{where}.error.message: must be a string")
        return code
    if entry.kind != "method" or entry.returns is None:
        reporter.error(line_of(response), f"{where}: only method docs may show a result")
        return None
    for problem in validator.check(response["result"], entry.returns, f"{where}.result", strict=True):
        reporter.error(line_of(response), problem)
    return None


def _check_event_message(entry, event, label, line, validator, reporter, warnings) -> None:
    where = f"{label}.event"
    if not isinstance(event, dict):
        reporter.error(line, f"{where}: must be a mapping")
        return
    for key in sorted(set(event) - EVENT_MESSAGE_KEYS):
        reporter.error(line_of(event), f"{where}: unknown key `{key}`")
    method = event.get("method")
    if not isinstance(method, str) or not ENTRY_NAME_RE.fullmatch(method):
        reporter.error(line_of(event), f"{where}.method: must be `Domain.event`")
        return
    if entry.kind == "event":
        if method != entry.key:
            reporter.error(line_of(event), f"{where}.method: expected {entry.key}, got {method!r}")
            return
    elif method not in entry.event_refs:
        reporter.error(line_of(event), f"{where}.method: {method} is not listed in `events`")
        return
    schema = validator.event_schema(method)
    if schema is None:
        warnings.append(f"{reporter.path}: {where} for {method} is not validated; event has no contract")
        return
    params = event.get("params")
    if params is None:
        params = {}
    for problem in validator.check(params, schema, f"{where}.params", strict=True):
        reporter.error(line_of(event), problem)


# ---------------------------------------------------------------------------
# Runtime schema export


def export_runtime_schema(
    contracts: ContractSet, methods: list[tuple[str, str]], domains: set[str]
) -> dict:
    """Export the effective contract for the C++ CDPContractChecker.

    Covers every supported method, every documented event, and the upstream
    events of supported domains, plus the closure of referenced upstream types.
    Docs-only constraints (`default`, `minimum`, `maximum`) are not exported;
    runtime checks only cover wire shapes.

    Compact keys: `k` kinds, `r` ref, `e` enum, `i` items, `p` properties
    (`s` schema, `o` optional), `a` map values.
    """

    upstream = contracts.upstream
    referenced: set[str] = set()

    def visit(schema: Optional[Schema]) -> Optional[dict]:
        if schema is None:
            return None
        if schema.ref is not None:
            if schema.ref not in referenced:
                referenced.add(schema.ref)
                target = upstream.types.get(schema.ref)
                if target is None:
                    raise ContractError(f"unresolved type reference {schema.ref}")
                types[schema.ref] = visit(target)
            return {"r": schema.ref}
        out: dict = {"k": sorted(schema.kinds)}
        if schema.enum is not None:
            out["e"] = list(schema.enum)
        if schema.items is not None:
            out["i"] = visit(schema.items)
        if schema.properties is not None:
            out["p"] = {
                name: {"s": visit(prop.schema), **({"o": 1} if prop.optional else {})}
                for name, prop in schema.properties.items()
            }
        if schema.additional is not None:
            out["a"] = visit(schema.additional)
        return out

    types: dict[str, dict] = {}
    commands: dict[str, dict] = {}
    for key in sorted(set(methods)):
        entry = contracts.entry("method", *key)
        if entry is None:
            continue
        command = {"p": visit(entry.parameters), "r": visit(entry.returns)}
        if entry.params_optional:
            command["po"] = 1
        commands[".".join(key)] = command

    events: dict[str, dict] = {}
    if upstream is not None:
        for (domain, name), entry in upstream.events.items():
            if domain in domains:
                events[f"{domain}.{name}"] = {"p": visit(entry.parameters)}
    for (domain, name), entry in contracts.events.items():
        events[f"{domain}.{name}"] = {"p": visit(entry.parameters)}

    return {
        "types": dict(sorted(types.items())),
        "commands": commands,
        "events": dict(sorted(events.items())),
    }

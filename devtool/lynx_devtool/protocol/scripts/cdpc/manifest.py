# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""cdp_manifest.generated.yaml (version 3): build it, and read the checked-in
one back for `since` history and PrimJS rows."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from cdpc import contract
from cdpc.repo import CdpcError, Paths, relative_path
from cdpc.scan import LocalMethod


MANIFEST_VERSION = 3


UPSTREAM_ROOT = "https://chromedevtools.github.io/devtools-protocol/tot"


V8_CDP_ROOT = "https://chromedevtools.github.io/devtools-protocol/1-3"


@dataclass(frozen=True)
class ExistingManifest:
    domains: set[str]
    methods: set[tuple[str, str]]
    events: set[tuple[str, str]]
    method_since: dict[tuple[str, str], str]
    event_since: dict[tuple[str, str], str]
    method_source: dict[tuple[str, str], str] = field(default_factory=dict)


def load_existing_manifest(manifest_path: Path) -> ExistingManifest:
    """Read prior generated metadata so new writes can preserve `since` history."""

    domains: set[str] = set()
    methods: set[tuple[str, str]] = set()
    events: set[tuple[str, str]] = set()
    method_since: dict[tuple[str, str], str] = {}
    event_since: dict[tuple[str, str], str] = {}
    method_source: dict[tuple[str, str], str] = {}

    if not manifest_path.exists():
        return ExistingManifest(
            domains=domains,
            methods=methods,
            events=events,
            method_since=method_since,
            event_since=event_since,
            method_source=method_source,
        )

    current_domain: Optional[str] = None
    current_section: Optional[str] = None
    current_entry: Optional[tuple[str, str]] = None

    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        domain_match = re.fullmatch(r"  - name: (.+)", line)
        if domain_match:
            current_domain = parse_yaml_scalar(domain_match.group(1))
            domains.add(current_domain)
            current_section = None
            current_entry = None
            continue

        if current_domain is None:
            continue

        section_match = re.fullmatch(r"    (methods|events|deviations):", line)
        if section_match:
            current_section = section_match.group(1)
            current_entry = None
            continue
        if current_section == "deviations":
            continue

        entry_match = re.fullmatch(r"      - name: (.+)", line)
        if entry_match and current_section:
            current_entry = (
                current_domain,
                parse_yaml_scalar(entry_match.group(1)),
            )
            if current_section == "methods":
                methods.add(current_entry)
            else:
                events.add(current_entry)
            continue

        entry_since_match = re.fullmatch(r"        since: (.+)", line)
        if entry_since_match and current_section and current_entry:
            since = parse_yaml_scalar(entry_since_match.group(1))
            if current_section == "methods":
                method_since[current_entry] = since
            else:
                event_since[current_entry] = since
            continue

        entry_source_match = re.fullmatch(r"        source: (.+)", line)
        if entry_source_match and current_section == "methods" and current_entry:
            method_source[current_entry] = parse_yaml_scalar(entry_source_match.group(1))

    return ExistingManifest(
        domains=domains,
        methods=methods,
        events=events,
        method_since=method_since,
        event_since=event_since,
        method_source=method_source,
    )


def load_upstream_methods(schema_path: Path) -> dict[str, set[str]]:
    if not schema_path.exists():
        raise CdpcError(
            f"missing vendored CDP schema: {schema_path}\n"
            "Add devtool/lynx_devtool/protocol/cdp_upstream/protocol.json first."
        )
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CdpcError(f"invalid JSON in {schema_path}: {exc}") from exc

    domains = schema.get("domains")
    if not isinstance(domains, list):
        raise CdpcError(f"{schema_path} must contain a top-level domains array")

    upstream: dict[str, set[str]] = {}
    for domain_entry in domains:
        if not isinstance(domain_entry, dict):
            continue
        domain = domain_entry.get("domain")
        if not isinstance(domain, str):
            continue
        commands = domain_entry.get("commands", [])
        if not isinstance(commands, list):
            commands = []
        methods = {
            command["name"]
            for command in commands
            if isinstance(command, dict) and isinstance(command.get("name"), str)
        }
        upstream[domain] = methods
    return upstream


def build_manifest(
    paths: Paths,
    local_methods: list[LocalMethod],
    scopes: dict[str, str],
    upstream_methods: dict[str, set[str]],
    existing_manifest: ExistingManifest,
    current_lynx_version: str,
    contracts: Optional[contract.ContractSet] = None,
) -> str:
    domains: dict[str, list[LocalMethod]] = {}
    for method in local_methods:
        domains.setdefault(method.domain, []).append(method)

    missing_scope = sorted(domain for domain in domains if domain not in scopes)
    if missing_scope:
        raise CdpcError(
            "methods found for unregistered domains: " + ", ".join(missing_scope)
        )

    method_contracts = contracts.methods if contracts else {}
    events: dict[str, list[contract.ContractEntry]] = {}
    deviations: dict[str, list[contract.Deviation]] = {}
    if contracts:
        for entry in contracts.events.values():
            events.setdefault(entry.domain, []).append(entry)
        for deviation in contracts.deviations:
            deviations.setdefault(deviation.domain, []).append(deviation)
    unknown_domains = sorted(
        domain for domain in [*events, *deviations] if domain not in scopes
    )
    if unknown_domains:
        raise CdpcError(
            "events or deviations declared for unregistered domains: "
            + ", ".join(sorted(set(unknown_domains)))
        )

    lines = [
        "# Copyright 2026 The Lynx Authors. All rights reserved.",
        "# Licensed under the Apache License Version 2.0 that can be found in the",
        "# LICENSE file in the root directory of this source tree.",
        "#",
        "# Generated by `tools/cdpc write`. Do not edit by hand.",
        "",
        f"version: {MANIFEST_VERSION}",
        "generatedFrom:",
        "  chromeDevToolsProtocol: cdp_upstream/protocol.json",
        f"upstreamRoot: {yaml_scalar(UPSTREAM_ROOT)}",
        "externalReferences:",
        "  v8:",
        "    displayName: V8",
        f"    cdpRoot: {yaml_scalar(V8_CDP_ROOT)}",
        "domains:",
    ]

    for domain in sorted({*domains, *events, *deviations}):
        domain_origin = "standard" if domain in upstream_methods else "lynx-extension"
        lines.extend(
            [
                f"  - name: {yaml_scalar(domain)}",
                f"    origin: {domain_origin}",
                f"    scope: {scopes[domain]}",
            ]
        )
        if domains.get(domain):
            lines.append("    methods:")
        for method in sorted(domains.get(domain, []), key=lambda item: item.method):
            method_contract = method_contracts.get((method.domain, method.method))
            method_since = entry_since(
                key=(method.domain, method.method),
                existing_entries=existing_manifest.methods,
                existing_since=existing_manifest.method_since,
                current_lynx_version=current_lynx_version,
                declared_since=method_contract.since if method_contract else None,
            )
            lines.extend(
                [
                    f"      - name: {yaml_scalar(method.method)}",
                    f"        origin: {classify_method(method, upstream_methods)}",
                ]
            )
            lines.extend(lifecycle_lines(method_contracts.get((method.domain, method.method))))
            lines.extend(
                [
                    f"        since: {yaml_quoted_string(method_since)}",
                    f"        source: {yaml_scalar(method.source)}",
                ]
            )
        if events.get(domain):
            lines.append("    events:")
        for event in sorted(events.get(domain, []), key=lambda item: item.name):
            event_since = entry_since(
                key=(event.domain, event.name),
                existing_entries=existing_manifest.events,
                existing_since=existing_manifest.event_since,
                current_lynx_version=current_lynx_version,
                declared_since=event.since,
            )
            lines.extend(
                [
                    f"      - name: {yaml_scalar(event.name)}",
                    f"        origin: {event.origin}",
                ]
            )
            lines.extend(lifecycle_lines(event))
            lines.extend(
                [
                    f"        since: {yaml_quoted_string(event_since)}",
                    f"        source: {yaml_scalar(relative_path(paths, event.source))}",
                ]
            )
        if deviations.get(domain):
            lines.append("    deviations:")
        for deviation in deviations.get(domain, []):
            lines.extend(
                [
                    f"      - target: {yaml_scalar(deviation.target)}",
                    f"        kind: {deviation.kind}",
                ]
            )
            if deviation.as_spec is not None:
                lines.append("        as:")
                lines.extend(yaml_mapping_lines(deviation.as_spec, indent=10))
            lines.append(f"        reason: {yaml_scalar(deviation.reason)}")
    lines.append("")
    return "\n".join(lines)


def lifecycle_lines(entry: Optional[contract.ContractEntry]) -> list[str]:
    if entry is None:
        return []
    lines = []
    if entry.experimental:
        lines.append("        experimental: true")
    if entry.deprecated:
        lines.append("        deprecated: true")
    return lines


def yaml_mapping_lines(mapping: dict, indent: int) -> list[str]:
    prefix = " " * indent
    lines = []
    for key, value in mapping.items():
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            lines.extend(yaml_mapping_lines(value, indent + 2))
        elif isinstance(value, list):
            lines.append(f"{prefix}{key}: [{', '.join(yaml_value(item) for item in value)}]")
        else:
            lines.append(f"{prefix}{key}: {yaml_value(value)}")
    return lines


def yaml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return yaml_scalar(str(value))


def entry_since(
    key,
    existing_entries,
    existing_since,
    current_lynx_version: str,
    declared_since: Optional[str] = None,
) -> str:
    """Pick an entry's `since`.

    A `since` declared in the entry's docs file is authoritative; otherwise keep
    the checked-in history, and assign the current Lynx version to new entries.
    """

    if declared_since is not None:
        return declared_since
    if key in existing_since:
        return existing_since[key]
    if key not in existing_entries:
        return current_lynx_version
    raise CdpcError(
        f"existing manifest entry {format_manifest_entry_key(key)} "
        "is missing mandatory `since`"
    )


def format_manifest_entry_key(key) -> str:
    if isinstance(key, tuple):
        return ".".join(key)
    return key


def classify_method(
    method: LocalMethod, upstream_methods: dict[str, set[str]]
) -> str:
    upstream_domain_methods = upstream_methods.get(method.domain)
    if upstream_domain_methods is None:
        return "lynx-extension"
    if method.method in upstream_domain_methods:
        return "standard"
    return "lynx-extension"


def yaml_scalar(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:#-]+", value):
        return value
    return json.dumps(value)


def yaml_quoted_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def parse_yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise CdpcError(
                f"invalid generated YAML string scalar: {value}"
            ) from exc
        if not isinstance(loaded, str):
            raise CdpcError(f"expected generated YAML string scalar: {value}")
        return loaded
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value

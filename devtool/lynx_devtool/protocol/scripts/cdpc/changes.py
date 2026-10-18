# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Field-level differences between an upstream entry and its Lynx contract.

`entry_changes` compares the pristine upstream shape with the effective one
(deviations and docs additions applied) and lists docs fields the contract
ignores. `diff` reports them per domain; `show` labels each field with them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from cdpc import contract
from cdpc.contract import ContractSet, Schema, Upstream


CHANGE_ORDER = ("added", "omitted", "retyped", "ignored")


@dataclass
class FieldDiff:
    entry: str  # "Domain.name"
    path: str  # "parameters.a.b" or "returns.a[]"
    change: str  # one of CHANGE_ORDER
    detail: str
    source: str  # "cdp_support.yaml: <reason>" or a docs path


def entry_changes(
    pristine: Upstream,
    contracts: ContractSet,
    kind: str,
    domain: str,
    name: str,
    display: Callable[[Path], str],
) -> list[FieldDiff]:
    """Field differences of one upstream method or event from its upstream shape."""

    pristine_entries = pristine.commands if kind == "method" else pristine.events
    if (domain, name) not in pristine_entries:
        return []
    effective = contracts.upstream
    effective_entries = effective.commands if kind == "method" else effective.events
    doc = (contracts.methods if kind == "method" else contracts.events).get((domain, name))
    reasons = {(item.domain, item.target): item.reason for item in contracts.deviations}
    support = display(contracts.support_file) if contracts.support_file else "cdp_support.yaml"
    origin = display(doc.source) if doc is not None else support
    key = f"{domain}.{name}"

    changes = []
    for section in ("parameters", "returns") if kind == "method" else ("parameters",):
        before = getattr(pristine_entries[(domain, name)], section)
        after = getattr(doc if doc is not None else effective_entries[(domain, name)], section)
        for change, path, detail in _compare(before, after, section):
            reason = reasons.get((domain, f"{name}.{path}"))
            source = f"{support}: {reason}" if reason else origin
            changes.append(FieldDiff(key, path, change, detail, source))
        declared = getattr(doc, f"declared_{section}", None) if doc is not None else None
        if declared is not None:
            for path, detail in _ignored(declared, after, section, effective, None):
                changes.append(FieldDiff(key, path, "ignored", detail, origin))
    return changes


def _compare(before: Optional[Schema], after: Optional[Schema], path: str):
    """Yield (change, path, detail) between two inline object schemas."""

    if before is None or after is None:
        return
    old = before.properties or {}
    new = after.properties or {}
    for name in new:
        if name not in old:
            yield "added", f"{path}.{name}", describe_property(new[name])
    for name in old:
        if name not in new:
            yield "omitted", f"{path}.{name}", describe_property(old[name])
    for name in old:
        if name not in new:
            continue
        old_prop, new_prop = old[name], new[name]
        old_type, new_type = describe(old_prop.schema), describe(new_prop.schema)
        if old_type != new_type:
            yield "retyped", f"{path}.{name}", f"{old_type} -> {new_type}"
        elif old_prop.optional != new_prop.optional:
            yield (
                "retyped",
                f"{path}.{name}",
                f"{_optional(old_prop.optional)} -> {_optional(new_prop.optional)}",
            )
        elif _inline_object(old_prop.schema) and _inline_object(new_prop.schema):
            yield from _compare(old_prop.schema, new_prop.schema, f"{path}.{name}")


def _ignored(
    declared: Schema,
    shape: Optional[Schema],
    path: str,
    upstream: Upstream,
    via_ref: Optional[str],
):
    """Yield (path, detail) for docs fields that are not in the contract shape."""

    shape, via_ref = _resolve(shape, upstream, via_ref)
    if shape is None:
        return
    known = shape.properties
    for name, prop in (declared.properties or {}).items():
        child = f"{path}.{name}"
        if known is None:
            # Any-object upstream field: docs may describe what Lynx puts in it.
            continue
        if name not in known:
            where = f"inside $ref {via_ref}" if via_ref else "nested in an upstream field"
            yield child, (
                f"{describe_property(prop)}; {where}, so the shape keeps the "
                "upstream shape and the runtime checker rejects it"
            )
            continue
        yield from _ignored(prop.schema, known[name].schema, child, upstream, via_ref)
    if declared.items is not None and shape.items is not None:
        yield from _ignored(declared.items, shape.items, f"{path}[]", upstream, via_ref)


def _resolve(schema: Optional[Schema], upstream: Upstream, via_ref: Optional[str]):
    seen = set()
    while schema is not None and schema.ref is not None and schema.ref not in seen:
        seen.add(schema.ref)
        via_ref = schema.ref
        schema = upstream.types.get(schema.ref)
    return schema, via_ref


def _inline_object(schema: Schema) -> bool:
    return schema.ref is None and schema.properties is not None


def _optional(value: bool) -> str:
    return "optional" if value else "required"


def describe_property(prop: contract.Property) -> str:
    text = describe(prop.schema)
    return f"{text}, optional" if prop.optional else text


def describe(schema: Schema) -> str:
    if schema.ref is not None:
        return schema.ref
    if schema.items is not None:
        return f"array<{describe(schema.items)}>"
    kinds = "|".join(sorted(schema.kinds)) or "any"
    if schema.enum is not None:
        return f"{kinds} enum({', '.join(schema.enum)})"
    return kinds


# ---------------------------------------------------------------------------
# Text output

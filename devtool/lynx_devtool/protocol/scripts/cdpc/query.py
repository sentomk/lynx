# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""`cdpc list` and `cdpc show`: query the CDP entries Lynx implements.

`list` covers implemented methods (the manifest inventory) and events that are
documented or named in Lynx sources. `show` prints one entry's effective
contract: the upstream shape with deviations and docs additions applied, each
field labeled with where it comes from.

`from_inventory` adapts a loaded Inventory; the rest takes plain data.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from cdpc import changes, contract, style
from cdpc.contract import ContractSet, Schema, Upstream
from cdpc.inventory import Inventory


UPSTREAM_ROOT = "https://chromedevtools.github.io/devtools-protocol/tot"


@dataclass
class Entry:
    domain: str
    name: str
    kind: str  # "method" or "event"
    origin: str  # "standard" or "lynx-extension"
    scope: str  # "global", "instance", "global+instance" or "-"
    docs: Optional[str]
    # Methods: the file that dispatches it. Events: the first source naming it.
    source: Optional[str]
    typed: bool

    @property
    def key(self) -> str:
        return f"{self.domain}.{self.name}"


def collect(
    contracts: ContractSet,
    pristine: Upstream,
    methods: Iterable[tuple[str, str, str]],
    sent_events: dict[tuple[str, str], str],
    scopes: dict[str, str],
    display: Callable[[Path], str],
) -> list[Entry]:
    """`methods` holds (domain, name, source) of every implemented method."""

    entries = []
    for domain, name, source in methods:
        doc = contracts.methods.get((domain, name))
        entries.append(
            Entry(
                domain,
                name,
                "method",
                "standard" if (domain, name) in pristine.commands else "lynx-extension",
                scopes.get(domain, "-"),
                display(doc.source) if doc else None,
                source,
                domain in contracts.typed_domains,
            )
        )
    for domain, name in sorted(set(contracts.events) | set(sent_events)):
        doc = contracts.events.get((domain, name))
        entries.append(
            Entry(
                domain,
                name,
                "event",
                "standard" if (domain, name) in pristine.events else "lynx-extension",
                scopes.get(domain, "-"),
                display(doc.source) if doc else None,
                sent_events.get((domain, name)),
                domain in contracts.typed_domains,
            )
        )
    return sorted(entries, key=lambda entry: (entry.domain, entry.name, entry.kind))


def from_inventory(found: Inventory) -> list[Entry]:
    """Entries for every implemented method and sent or documented event."""

    return collect(
        found.contracts,
        found.pristine,
        [(item.domain, item.method, item.source) for item in found.methods],
        found.sent_events,
        found.scopes,
        found.display,
    )


def select(
    entries: list[Entry],
    patterns: Iterable[str] = (),
    kind: Optional[str] = None,
    origin: Optional[str] = None,
    undocumented: bool = False,
) -> list[Entry]:
    """Filter by `Domain`, `Domain.name` or a glob such as `DOM.get*`."""

    globs = [pattern if "." in pattern else f"{pattern}.*" for pattern in patterns]
    return [
        entry
        for entry in entries
        if (not globs or any(fnmatch.fnmatchcase(entry.key, glob) for glob in globs))
        and (kind is None or entry.kind == kind)
        and (origin is None or entry.origin == origin)
        and (not undocumented or entry.docs is None)
    ]


def format_list(entries: list[Entry]) -> str:
    if not entries:
        return "No matching CDP entries.\n"
    rows = [("NAME", "KIND", "ORIGIN", "SCOPE", "DOCS", "SOURCE")]
    for entry in entries:
        rows.append(
            (
                entry.key,
                entry.kind,
                ("standard" if entry.origin == "standard" else "lynx")
                + (" typed" if entry.typed else ""),
                entry.scope,
                "yes" if entry.docs else "no",
                entry.source or "-",
            )
        )
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]) - 1)]
    lines = []
    for index, row in enumerate(rows):
        # Pad the plain text first; color codes would break the widths.
        cells = [cell.ljust(width) for cell, width in zip(row, widths)] + [row[-1]]
        if index == 0:
            left = style.bold("  ".join(cells[:5])) + "  "
            lines.append(style.wrap(row[5], left, None, "bold"))
            continue
        cells[0] = style.paint(cells[0], "bold", "cyan")
        if row[2].startswith("lynx"):
            cells[2] = style.paint(cells[2], "magenta")
        cells[4] = style.paint(cells[4], "green" if row[4] == "yes" else "yellow")
        # The source column wraps under itself.
        left = "  ".join(cells[:5]) + "  "
        lines.append(style.wrap(row[5], left, sum(widths) + 2 * 5, "blue"))
    methods = sum(entry.kind == "method" for entry in entries)
    undocumented = sum(entry.docs is None for entry in entries)
    lines.append("")
    lines.append(
        f"-- {methods} methods, {len(entries) - methods} events, "
        + style.paint(f"{undocumented} without docs", "yellow" if undocumented else "")
    )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# show


def show(
    query: str,
    entries: list[Entry],
    contracts: ContractSet,
    pristine: Upstream,
    display: Callable[[Path], str],
) -> Optional[str]:
    """The effective contract of `Domain.name`, or None when nothing matches.

    An entry that is unknown to Lynx but exists upstream is still shown, so the
    upstream shape can be looked up before implementing it.
    """

    domain, _, name = query.partition(".")
    blocks = []
    for kind in ("method", "event"):
        entry = next(
            (item for item in entries if item.key == query and item.kind == kind), None
        )
        docs = contracts.methods if kind == "method" else contracts.events
        upstream = pristine.commands if kind == "method" else pristine.events
        if entry is None and (domain, name) not in docs and (domain, name) not in upstream:
            continue
        blocks.append(
            _show_entry(kind, domain, name, entry, contracts, pristine, display)
        )
    return "\n".join(blocks) if blocks else None


def _show_entry(
    kind: str,
    domain: str,
    name: str,
    entry: Optional[Entry],
    contracts: ContractSet,
    pristine: Upstream,
    display: Callable[[Path], str],
) -> str:
    doc = (contracts.methods if kind == "method" else contracts.events).get((domain, name))
    upstream_entries = pristine.commands if kind == "method" else pristine.events
    standard = (domain, name) in upstream_entries
    origin = "standard" if standard else "lynx-extension"
    scope = entry.scope if entry else "-"
    header = f"{kind}, {origin}" + (f", {scope} scope" if scope != "-" else "")
    lines = [style.header(f"{domain}.{name}") + "  " + style.dim(f"({header})")]

    def row(label: str, value: str, *styles: str) -> None:
        lines.append(style.wrap(value, f"  {style.dim(f'{label}:'.ljust(13))}", 15, *styles))

    if kind == "method":
        if entry:
            row("implemented", entry.source)
        else:
            row("implemented", "no", "yellow")
    else:
        if domain in contract.ENGINE_DOMAINS:
            row("sent from", "the JS engine")
        else:
            row("sent from", entry.source if entry and entry.source else "not found in sources")
    if doc:
        row("docs", display(doc.source))
    else:
        row("docs", "none", "yellow")
    row("codegen", "typed" if domain in contracts.typed_domains else "none")
    if doc is not None:
        lifecycle = [flag for flag in ("experimental", "deprecated") if getattr(doc, flag)]
        if doc.since:
            lifecycle.insert(0, f"since {doc.since}")
        if lifecycle:
            row("lifecycle", ", ".join(lifecycle))
        if doc.summary:
            row("summary", doc.summary)
    if standard:
        row("upstream", f"{UPSTREAM_ROOT}/{domain}/#{kind}-{name}")
    if domain in contract.ENGINE_DOMAINS:
        row("note", "forwarded to the JS engine; Lynx does not decode it")

    effective = contracts.upstream.commands if kind == "method" else contracts.upstream.events
    shape = doc if doc is not None else effective.get((domain, name))
    field_changes = changes.entry_changes(pristine, contracts, kind, domain, name, display)
    support = display(contracts.support_file) if contracts.support_file else "cdp_support.yaml"
    by_path = {change.path: change for change in field_changes}
    sections = ("parameters", "returns") if kind == "method" else ("parameters",)
    if shape is None:
        lines.append("  no contract: the entry has no docs and no upstream definition")
    for section in sections if shape is not None else ():
        schema = getattr(shape, section)
        fields = list(_fields(schema, section)) if schema is not None else []
        if schema is not None and not _is_object(schema):
            label = "params" if section == "parameters" else "result"
            lines.append(
                f"  {style.bold(section)}: the whole {label} is "
                f"{changes.describe(schema)}"
            )
            continue
        if not fields:
            lines.append(f"  {style.bold(section)}: none")
            continue
        lines.append(f"  {style.bold(section)}:")
        table = []
        for path, text in fields:
            provenance = _provenance(path, by_path, standard, support)
            table.append((path.split(".", 1)[1], text, provenance))
        for change in field_changes:
            if change.path.startswith(f"{section}.") and change.change in ("omitted", "ignored"):
                table.append(
                    (change.path.split(".", 1)[1], change.detail, _label(change, support))
                )
        width = max(len(path) for path, _, _ in table)
        type_width = min(max(len(text) for _, text, _ in table), 40)
        for path, text, provenance in table:
            left = f"    {style.paint(path.ljust(width), 'cyan')}  {text:<{type_width}}  "
            lines.append(
                style.wrap(
                    provenance, left, 4 + width + 2 + max(type_width, len(text)) + 2,
                    *_provenance_styles(provenance),
                )
            )
    refs = sorted(
        {
            schema.ref
            for section in (sections if shape is not None else ())
            if getattr(shape, section) is not None
            for schema in _refs(getattr(shape, section))
        }
    )
    if refs:
        lines.append(f"  {style.bold('types')}:")
        for ref in refs:
            target = contracts.upstream.types.get(ref) if contracts.upstream else None
            if target is None or not target.properties:
                text = changes.describe(target) if target else "unknown"
                lines.append(f"    {ref}: {text}")
                continue
            lines.append(f"    {style.paint(ref, 'cyan')}:")
            fields = [(path.split(".", 1)[1], text) for path, text in _fields(target, "type")]
            width = max(len(path) for path, _ in fields)
            for path, text in fields:
                lines.append(
                    style.wrap(text, f"      {style.paint(path.ljust(width), 'cyan')}  ")
                )
    if doc is not None and doc.event_refs:
        row("events", ", ".join(doc.event_refs))
    if doc is not None and doc.examples:
        row("examples", f"{len(doc.examples)} in docs")
    lines.append("")
    return "\n".join(lines)


def _fields(schema: Schema, path: str):
    """Yield (path, type text) of nested fields, `$ref` targets not expanded."""

    for name, prop in (schema.properties or {}).items():
        child = f"{path}.{name}"
        yield child, changes.describe_property(prop)
        yield from _nested(prop.schema, child)


def _nested(schema: Schema, path: str):
    if schema.ref is not None:
        return
    if schema.properties:
        yield from _fields(schema, path)
    if schema.items is not None:
        if schema.items.properties:
            yield from _fields(schema.items, f"{path}[]")
        elif schema.items.items is not None:
            yield from _nested(schema.items, f"{path}[]")
    if schema.additional is not None:
        yield f"{path}.<key>", changes.describe(schema.additional)
        yield from _nested(schema.additional, f"{path}.<key>")


def _refs(schema: Schema):
    if schema.ref is not None:
        yield schema
        return
    for prop in (schema.properties or {}).values():
        yield from _refs(prop.schema)
    for child in (schema.items, schema.additional):
        if child is not None:
            yield from _refs(child)


def _is_object(schema: Schema) -> bool:
    return schema.ref is None and schema.kinds == {"object"} and schema.properties is not None


def _provenance(path: str, by_path: dict, standard: bool, support: str) -> str:
    """Where a field comes from. Nested fields follow their top-level field."""

    section, rest = path.split(".", 1)
    top = f"{section}.{re.split(r'[.[]', rest, maxsplit=1)[0]}"
    change = by_path.get(path) or by_path.get(top)
    if change is not None and change.change in ("added", "retyped"):
        return _label(change, support)
    return "upstream" if standard else "docs"


def _label(change: changes.FieldDiff, support: str) -> str:
    prefix = f"{support}: "
    if change.source.startswith(prefix):
        text = f"{change.change} by deviation: {change.source[len(prefix):]}"
    elif change.change == "ignored":
        text = "ignored: in docs only, not in the contract"
    else:
        text = f"{change.change} in docs"
    symbol, _ = style.CHANGE_STYLE[change.change]
    return f"{symbol} {text}"


def _provenance_styles(text: str) -> tuple[str, ...]:
    for kind, (symbol, color) in style.CHANGE_STYLE.items():
        if text.startswith(f"{symbol} {kind}"):
            return (color,)
    return ("dim",) if text == "upstream" else ()

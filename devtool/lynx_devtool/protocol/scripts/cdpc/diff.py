# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""`cdpc diff`: how Lynx differs from the vendored upstream CDP schema.

For each standard domain Lynx touches, the report lists:

- upstream methods Lynx does not implement, Lynx-extension methods, and
  implemented upstream methods without docs, whose shape is taken from
  upstream unchecked;
- upstream events found in Lynx sources and the ones that are not, for
  domains Lynx implements itself (engine-forwarded domains are not scanned);
- field differences of upstream entries (see changes.py).
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from cdpc import changes, contract, style
from cdpc.changes import CHANGE_ORDER, FieldDiff
from cdpc.contract import ContractSet, Upstream
from cdpc.inventory import Inventory
from cdpc.repo import CdpcError


def run(inventory: Inventory, domains: list[str]) -> str:
    """The report for `domains`, or every standard domain Lynx touches."""

    unknown = sorted(set(domains) - inventory.pristine.domains)
    if unknown:
        raise CdpcError("not upstream CDP domains: " + ", ".join(unknown))
    report = build_report(
        inventory.pristine,
        inventory.contracts,
        [(item.domain, item.method) for item in inventory.methods],
        [key for key in inventory.sent_events if key in inventory.pristine.events],
        inventory.display,
        domains=domains or None,
    )
    return format_report(report)


@dataclass
class DomainDiff:
    domain: str
    engine: bool
    upstream_methods: int = 0
    missing_methods: list[str] = field(default_factory=list)
    extension_methods: list[str] = field(default_factory=list)
    undocumented_methods: list[str] = field(default_factory=list)
    upstream_events: int = 0
    sent_events: list[str] = field(default_factory=list)
    unsent_events: list[str] = field(default_factory=list)
    extension_events: list[str] = field(default_factory=list)
    fields: list[FieldDiff] = field(default_factory=list)


def build_report(
    pristine: Upstream,
    contracts: ContractSet,
    implemented_methods: Iterable[tuple[str, str]],
    sent_events: Iterable[tuple[str, str]],
    display: Callable[[Path], str],
    domains: Optional[Iterable[str]] = None,
) -> list[DomainDiff]:
    """Compare `pristine` (upstream without deviations) with the Lynx contract.

    `contracts` must be loaded against a separate Upstream instance, which has
    the deviations applied in place.
    """

    implemented = set(implemented_methods)
    sent = set(sent_events)

    touched = (
        {domain for domain, _ in implemented}
        | {domain for domain, _ in sent}
        | {domain for domain, _ in [*contracts.methods, *contracts.events]}
        | {item.domain for item in contracts.deviations}
    ) & pristine.domains
    selected = sorted(touched if domains is None else set(domains) & pristine.domains)

    report = []
    for domain in selected:
        diff = DomainDiff(domain=domain, engine=domain in contract.ENGINE_DOMAINS)
        upstream_methods = sorted(name for d, name in pristine.commands if d == domain)
        upstream_events = sorted(name for d, name in pristine.events if d == domain)
        local = {name for d, name in implemented if d == domain}
        diff.upstream_methods = len(upstream_methods)
        diff.missing_methods = [name for name in upstream_methods if name not in local]
        diff.extension_methods = sorted(local - set(upstream_methods))
        diff.undocumented_methods = [
            name
            for name in upstream_methods
            if name in local and (domain, name) not in contracts.methods
        ]
        diff.upstream_events = len(upstream_events)
        if not diff.engine:
            diff.sent_events = [name for name in upstream_events if (domain, name) in sent]
            diff.unsent_events = [name for name in upstream_events if (domain, name) not in sent]
        diff.extension_events = sorted(
            name
            for (d, name), entry in contracts.events.items()
            if d == domain and entry.origin == "lynx-extension"
        )

        for kind, names in (("method", upstream_methods), ("event", upstream_events)):
            for name in names:
                diff.fields.extend(
                    changes.entry_changes(pristine, contracts, kind, domain, name, display)
                )
        report.append(diff)
    return report


def format_report(report: list[DomainDiff]) -> str:
    """Text report. Lines carry symbols (+ - ~ ! ?) so color is optional."""

    lines = [style.bold("Lynx vs upstream CDP (cdp_upstream/protocol.json)"), ""]
    totals = {change: 0 for change in CHANGE_ORDER}
    for diff in report:
        title = style.header(diff.domain)
        if diff.engine:
            title += style.dim("  engine-forwarded")
        lines.append(title)
        implemented = diff.upstream_methods - len(diff.missing_methods)
        lines.append(
            f"  {style.bold('methods')}  {implemented} of {diff.upstream_methods} "
            "upstream implemented"
        )
        _names(lines, "-", "not implemented", diff.missing_methods, "yellow")
        _names(lines, "+", "Lynx extensions", diff.extension_methods, "green")
        _names(
            lines, "?", "no docs, upstream shape assumed", diff.undocumented_methods,
            "blue",
        )
        if diff.engine and diff.upstream_events:
            lines.append(
                f"  {style.bold('events')}   {diff.upstream_events} upstream; "
                "sent by the JS engine, not scanned"
            )
        elif diff.upstream_events:
            lines.append(
                f"  {style.bold('events')}   {len(diff.sent_events)} of "
                f"{diff.upstream_events} upstream named in Lynx sources"
            )
            _names(lines, "*", "named in sources", diff.sent_events, "")
            _names(lines, "-", "not found", diff.unsent_events, "yellow")
        _names(lines, "+", "Lynx extensions", diff.extension_events, "green")
        if diff.fields:
            lines.append(f"  {style.bold('fields')}")
        for item in sorted(
            diff.fields, key=lambda f: (CHANGE_ORDER.index(f.change), f.entry, f.path)
        ):
            totals[item.change] += 1
            name = item.entry.split(".", 1)[1]
            label = style.change(item.change, f"{item.change:<8}")
            lines.append(style.wrap(f"{name}.{item.path}: {item.detail}", f"    {label} "))
            lines.append(style.wrap(item.source, " " * 15))
        lines.append("")
    count = f"{len(report)} domain{'' if len(report) == 1 else 's'}"
    parts = {
        change: f"{style.CHANGE_STYLE[change][0]} {totals[change]} {change}"
        for change in CHANGE_ORDER
    }
    summary = style.wrap(f"{count}; field differences: " + ", ".join(parts.values()), "-- ")
    # Color each count after wrapping; wrapping never splits one.
    for change, part in parts.items():
        summary = summary.replace(part, style.change(change, part[2:]), 1)
    lines.append(summary)
    lines.append("")
    return "\n".join(lines)


def _names(lines: list[str], symbol: str, label: str, names: list[str], color: str) -> None:
    if not names:
        return
    prefix = f"    {symbol} {label} ({len(names)}): "
    wrapped = textwrap.wrap(
        ", ".join(names),
        width=style.width(),
        initial_indent=prefix,
        subsequent_indent=" " * 6,
        break_on_hyphens=False,
    )
    # Wrap the plain text, then color only the label so widths stay right.
    head = style.paint(prefix.rstrip(), color) if color else prefix.rstrip()
    wrapped[0] = head + " " + _cyan_names(wrapped[0][len(prefix):])
    lines.extend(wrapped[:1] + [_cyan_names(line) for line in wrapped[1:]])


def _cyan_names(text: str) -> str:
    return re.sub(r"[A-Za-z_][A-Za-z0-9_]*", lambda m: style.paint(m.group(0), "cyan"), text)

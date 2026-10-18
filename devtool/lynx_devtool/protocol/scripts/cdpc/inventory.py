# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""What Lynx implements next to its contracts.

Loads the contracts, scans the sources, and resolves typed domains, whose
method inventory comes from their contracts instead of method tables.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from cdpc import contract, manifest, scan, style
from cdpc.repo import CdpcError, Paths, relative_path
from cdpc.scan import LocalMethod


def load_contracts(paths: Paths) -> contract.ContractSet:
    try:
        upstream = contract.load_upstream(paths.upstream_schema)
    except contract.ContractError as exc:
        raise CdpcError(str(exc)) from exc
    return contract.load_contracts(
        paths.custom_docs_dir,
        paths.support_file,
        upstream,
        lambda path: relative_path(paths, path),
    )


def report_contract_errors(contracts: contract.ContractSet) -> int:
    print(style.error("DevTool CDP contracts are invalid."), file=sys.stderr)
    for error in contracts.errors:
        print(style.item("x", error, "red"), file=sys.stderr)
    return 1


def report_contract_warnings(contracts: contract.ContractSet) -> None:
    if not contracts.warnings:
        return
    print(
        style.warning(
            f"DevTool CDP contracts have {len(contracts.warnings)} unverified item(s):"
        ),
        file=sys.stderr,
    )
    for warning in contracts.warnings:
        print(style.item("?", warning, "yellow"), file=sys.stderr)


@dataclass
class Inventory:
    """What Lynx implements, next to its contracts. Used by the query commands."""

    paths: Paths
    contracts: contract.ContractSet
    # Upstream without deviations; `contracts.upstream` has them applied.
    pristine: contract.Upstream
    methods: list[LocalMethod]
    scopes: dict[str, str]
    # Upstream and documented events named in Lynx sources.
    sent_events: dict[tuple[str, str], str]

    def display(self, path: Path) -> str:
        return relative_path(self.paths, path)


def load_inventory(
    paths: Paths, contracts: contract.ContractSet, require_primjs: bool
) -> Inventory:
    try:
        pristine = contract.load_upstream(paths.upstream_schema)
    except contract.ContractError as exc:
        raise CdpcError(str(exc)) from exc
    methods = scan_local_methods(paths, require_primjs)
    methods, leftovers = split_typed_leftovers(methods, contracts)
    if leftovers:
        # Queries keep working during a migration; write and check fail.
        print(style.warning(typed_leftovers_message(leftovers)), file=sys.stderr)
    methods = add_typed_methods(paths, methods, contracts)
    sent = scan.scan_event_references(
        [paths.source_root / "devtool", paths.source_root / "platform"],
        set(pristine.events) | set(contracts.events),
    )
    return Inventory(
        paths=paths,
        contracts=contracts,
        pristine=pristine,
        methods=methods,
        scopes=scan.scan_domain_scopes(paths.registration_source),
        sent_events={key: relative_path(paths, path) for key, path in sent.items()},
    )


def scan_local_methods(paths: Paths, require_primjs: bool) -> list[LocalMethod]:
    """Scan method tables; without PrimJS sources, reuse the manifest's PrimJS rows."""

    previous = manifest.load_existing_manifest(paths.manifest).method_source
    return scan.scan_local_methods(paths, previous, require_primjs=require_primjs)


def split_typed_leftovers(
    local_methods: list[LocalMethod], contracts: contract.ContractSet
) -> tuple[list[LocalMethod], list[LocalMethod]]:
    """Split off legacy declarations that a typed domain no longer dispatches."""

    leftovers = [item for item in local_methods if item.domain in contracts.typed_domains]
    kept = [item for item in local_methods if item.domain not in contracts.typed_domains]
    return kept, leftovers


def typed_leftovers_message(leftovers: list[LocalMethod]) -> str:
    return (
        "typed domains dispatch through the generated Agent; remove legacy "
        "method declarations:\n  "
        + "\n  ".join(f"{item.domain}.{item.method} in {item.source}" for item in leftovers)
    )


def fully_migrated(contracts: contract.ContractSet, domain: str) -> bool:
    """True when `legacy.migrated` names every method of a legacy-validation domain."""

    legacy = contracts.domain_legacy.get(domain)
    if legacy is None:
        return contract.domain_validation(contracts, domain) != "legacy"
    names = {
        entry.name
        for entry in contract.typed_entries(contracts, domain)
        if entry.kind == "method"
    }
    return bool(names) and set(legacy.migrated) >= names


def partition_leftovers(
    leftovers: list[LocalMethod], contracts: contract.ContractSet
) -> tuple[list[LocalMethod], list[LocalMethod]]:
    """Split leftovers into (errors, warnings).

    While `legacy.agent` still points at the old file and `migrated` does not
    cover every method, leftovers in that file are warnings. Leftovers anywhere
    else, and every leftover after the domain is fully migrated, stay errors.
    """

    errors: list[LocalMethod] = []
    warnings: list[LocalMethod] = []
    for item in leftovers:
        legacy = contracts.domain_legacy.get(item.domain)
        agent = legacy.agent if legacy is not None else None
        if (
            agent
            and not fully_migrated(contracts, item.domain)
            and (item.source == agent or item.source.endswith("/" + Path(agent).name))
        ):
            warnings.append(item)
        else:
            errors.append(item)
    return errors, warnings


def add_typed_methods(
    paths: Paths,
    local_methods: list[LocalMethod],
    contracts: contract.ContractSet,
) -> list[LocalMethod]:
    """Take typed domains' methods from their contracts.

    Typed domains dispatch through the generated Agent, so the contract is
    their method inventory. Each method's source is its docs file, or
    cdp_support.yaml for an undocumented standard method. Legacy declarations
    left in a typed domain are never reached and are rejected.
    """

    _, leftovers = split_typed_leftovers(local_methods, contracts)
    if leftovers:
        raise CdpcError(typed_leftovers_message(leftovers))
    typed = [
        LocalMethod(
            domain=entry.domain,
            method=entry.name,
            source=relative_path(paths, entry.source),
        )
        for domain in sorted(contracts.typed_domains)
        for entry in contract.typed_entries(contracts, domain)
        if entry.kind == "method"
    ]
    return sorted(local_methods + typed, key=lambda item: (item.domain, item.method))

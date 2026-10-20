# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""What Lynx implements next to its contracts.

Loads the contracts, scans the sources, and resolves dispatch domains, whose
method inventory comes from their contracts instead of method tables.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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
    # Preserve observed registrations separately from generated inventory rows.
    observed_methods: Optional[list[LocalMethod]] = None

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
    observed_methods = list(methods)
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
        observed_methods=observed_methods,
    )


def scan_local_methods(paths: Paths, require_primjs: bool) -> list[LocalMethod]:
    """Scan method tables; without PrimJS sources, reuse the manifest's PrimJS rows."""

    previous = manifest.load_existing_manifest(paths.manifest).method_source
    return scan.scan_local_methods(paths, previous, require_primjs=require_primjs)


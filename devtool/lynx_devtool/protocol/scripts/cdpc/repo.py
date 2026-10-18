# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Repository layout: where cdpc reads and writes files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class CdpcError(Exception):
    """A repository state cdpc cannot work with; reported without a traceback."""


@dataclass(frozen=True)
class Paths:
    source_root: Path
    protocol_dir: Path
    upstream_schema: Path
    manifest: Path
    custom_docs_dir: Path
    support_file: Path
    generated_header: Path
    generated_source: Path
    schema_header: Path
    schema_source: Path
    fixture_dir: Path
    clang_format: Path
    domain_agent_dir: Path
    registration_source: Path
    primjs_protocols_source: Path
    lynx_config_source: Path


def get_paths() -> Paths:
    script_path = Path(__file__).resolve()
    protocol_dir = script_path.parents[2]
    lynx_devtool_dir = protocol_dir.parent
    devtool_dir = lynx_devtool_dir.parent
    source_root = devtool_dir.parent
    return Paths(
        source_root=source_root,
        protocol_dir=protocol_dir,
        upstream_schema=protocol_dir / "cdp_upstream" / "protocol.json",
        manifest=protocol_dir / "cdp_manifest.generated.yaml",
        custom_docs_dir=protocol_dir / "custom_cdp_docs",
        support_file=protocol_dir / "cdp_support.yaml",
        generated_header=protocol_dir / "cdp_protocol.generated.h",
        generated_source=protocol_dir / "cdp_protocol.generated.cc",
        schema_header=protocol_dir / "cdp_contract_schema.generated.h",
        schema_source=protocol_dir / "cdp_contract_schema.generated.cc",
        fixture_dir=protocol_dir / "testdata" / "fixture",
        clang_format=source_root / "buildtools" / "llvm" / "bin" / "clang-format",
        domain_agent_dir=lynx_devtool_dir / "agent" / "domain_agent",
        registration_source=lynx_devtool_dir / "lynx_devtool_ng.cc",
        primjs_protocols_source=source_root
        / "third_party"
        / "quickjs"
        / "src"
        / "src"
        / "inspector"
        / "protocols.cc",
        lynx_config_source=source_root / "core" / "renderer" / "tasm" / "config.h",
    )


def relative_path(paths: Paths, path: Path) -> str:
    return path.resolve().relative_to(paths.source_root).as_posix()

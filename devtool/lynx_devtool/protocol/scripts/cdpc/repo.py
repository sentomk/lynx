# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Repository layout: where cdpc reads and writes files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


# The license header every Lynx source file starts with. The year is fixed so
# regenerated outputs stay byte-identical for `tools/cdpc check`.
LICENSE_LINES = (
    "Copyright 2026 The Lynx Authors. All rights reserved.",
    "Licensed under the Apache License Version 2.0 that can be found in the",
    "LICENSE file in the root directory of this source tree.",
)


def license_header(comment: str = "//") -> list[str]:
    """LICENSE_LINES behind `comment`, e.g. "//" for C++ or "#" for YAML."""

    return [f"{comment} {line}" for line in LICENSE_LINES]


class CdpcError(Exception):
    """A repository state cdpc cannot work with; reported without a traceback."""


class EnvError(CdpcError):
    """Missing toolchain, or a path this checkout cannot express. Exit status 3.

    Separated from other CdpcErrors so CI can tell a missing environment from
    a contract or generated-file failure.
    """


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

    @property
    def dispatch_header(self) -> Path:
        return self.generated_header.with_name("cdp_dispatch.generated.h")

    @property
    def dispatch_source(self) -> Path:
        return self.generated_source.with_name("cdp_dispatch.generated.cc")


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
    """Path relative to the source root, or EnvError when it cannot be expressed.

    `Path.resolve` follows symlinks. A symlink that leaves the checkout (common
    in git worktrees, for example `third_party/quickjs/src`) used to raise a
    bare ValueError from `relative_to`.
    """

    try:
        return path.resolve().relative_to(paths.source_root.resolve()).as_posix()
    except ValueError as exc:
        raise EnvError(
            f"cannot express {path} relative to the source root; it resolves "
            "outside the repository, often through a symlink. "
            f"({exc})"
        ) from exc

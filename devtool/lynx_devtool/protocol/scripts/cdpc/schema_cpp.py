# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""cdp_contract_schema.generated.{h,cc}: the effective contract as compact JSON
for CDPContractChecker, linked into test and debug targets only."""

from __future__ import annotations

import json
import re

from cdpc import contract
from cdpc.repo import Paths, relative_path
from cdpc.scan import LocalMethod


SCHEMA_CHUNK_BYTES = 12000
# Escaped characters per literal line, so 4-space indent + quotes + comma
# stay within 80 columns.


SCHEMA_LINE_WIDTH = 73
# Generated C++ is normalized with the clang-format from `tools/hab sync`.


def build(
    paths: Paths,
    contracts: contract.ContractSet,
    local_methods: list[LocalMethod],
    domains: set[str],
    notice: list[str],
) -> tuple[str, str]:
    """Return the schema header and source text, before clang-format."""

    schema = contract.export_runtime_schema(
        contracts, [(item.domain, item.method) for item in local_methods], domains
    )
    schema_json = json.dumps(schema, separators=(",", ":"), ensure_ascii=True)
    header_include = relative_path(paths, paths.schema_header)
    guard = re.sub(r"[^A-Za-z0-9]", "_", header_include).upper() + "_"

    header = notice + [
        f"#ifndef {guard}",
        f"#define {guard}",
        "",
        "#include <string>",
        "",
        "namespace lynx {",
        "namespace devtool {",
        "namespace protocol {",
        "",
        "// Effective CDP contract in the compact JSON form read by",
        "// CDPContractChecker. Only test and debug targets link this data.",
        "std::string GetContractSchemaJson();",
        "",
        "}  // namespace protocol",
        "}  // namespace devtool",
        "}  // namespace lynx",
        "",
        f"#endif  // {guard}",
        "",
    ]

    source = notice + [
        f'#include "{header_include}"',
        "",
        "namespace lynx {",
        "namespace devtool {",
        "namespace protocol {",
        "",
        "namespace {",
        "",
        "// Split into chunks below the MSVC string literal size limit.",
        "constexpr const char* kContractSchemaChunks[] = {",
    ]
    for start in range(0, len(schema_json), SCHEMA_CHUNK_BYTES):
        pieces = cpp_string_literals(
            schema_json[start : start + SCHEMA_CHUNK_BYTES], SCHEMA_LINE_WIDTH
        )
        source.extend(f"    {piece}" for piece in pieces[:-1])
        source.append(f"    {pieces[-1]},")
    source += [
        "};",
        "",
        "}  // namespace",
        "",
        "std::string GetContractSchemaJson() {",
        "  std::string schema;",
        "  for (const char* chunk : kContractSchemaChunks) {",
        "    schema += chunk;",
        "  }",
        "  return schema;",
        "}",
        "",
        "}  // namespace protocol",
        "}  // namespace devtool",
        "}  // namespace lynx",
        "",
    ]
    return "\n".join(header), "\n".join(source)


def cpp_string_literals(text: str, width: int) -> list[str]:
    """Split `text` into C++ string literals whose escaped body fits `width`."""

    pieces: list[str] = []
    current = ""
    for char in text:
        escaped = "\\" + char if char in '\\"?' else char
        if len(current) + len(escaped) > width:
            pieces.append(f'"{current}"')
            current = ""
        current += escaped
    pieces.append(f'"{current}"')
    return pieces

# CDPC toolchain and C++ generation checks

The independent `cdpc_core` package owns loading/merging, field semantics,
examples, normalized inventory checks, offline traces and schema graph data.
Lynx keeps `cdpc/model.py` as its path adapter, `inventory.py` as its source
scanner, the manifest/legacy parameter emitter, upstream downloading and capture.
The compatibility imports contain no second copy of the contract semantics.

`protocol/cdpc_toolchain.json` pins an exact CDPC commit and format versions.
`tools/cdpc` and `tools/cdpc.ps1` load that checkout through `cdpc_toolchain.py`.
Set `CDPC_SOURCE_DIR` to its location; the default is a sibling `cdpc` directory.
The command checks the revision and rejects dirty tool/runtime sources. It never
clones or downloads during a build. Update the pin intentionally when adopting
new tool changes. Python 3.10+ and PyYAML 6.x are required; the existing Lynx
`tools/env.sh` environment supplies them on supported developer setups.

The ordinary `tools/cdpc check/write/show/new/verify/capture/upstream` interface
is preserved. For standalone use, install CDPC's Python package and supply an
explicit config. To export Lynx's implementation evidence:

```sh
tools/cdpc runtime export --output-directory /tmp/cdpc-input
cdpc write --config /tmp/cdpc-input/cdpc.yaml
cdpc check --config /tmp/cdpc-input/cdpc.yaml
```

The exported config records source locations; the inventory records names,
locations, diagnostics and unverified domains. It is a snapshot: rerun export
after implementation registrations change. Contract JSON remains format v2;
the independent descriptor graph is v1 and retains scalar-root deviations.
The standalone CLI does not scan Lynx or refresh this inventory by itself.

## Generated C++ method contracts

The Lynx `cdpc_cpp.py` adapter consumes graph v1 and emits immutable C++17
`Schema`, `Property` and `MethodContract` storage. It owns the `cdpc/contract.h`
include and `lynx::devtool::cdpc::methods` namespace. For example, a handwritten
method table binds `methods::kInputInsertText` to the business handler. The
emitter generates no handler, Domain hierarchy, dispatch code or sender.
Recursive references use forward declarations and constant addresses. A binary
includes one generated catalog; filtered catalogs are standalone slices for
review/tests, not headers to combine. Inline storage has one identity across
translation units. Symbol collisions fail
explicitly. String literals preserve complete UTF-8 and embedded NUL values.

```sh
tools/cdpc runtime write --output-directory /tmp/cdpc-catalog --allow-unsupported
tools/cdpc runtime check --output-directory /tmp/cdpc-catalog --allow-unsupported

# A selected method is useful for reviewing the eventual API.
tools/cdpc runtime write Input.insertText --output-directory /tmp/cdpc-input-method
```

An output directory is always explicit. `write` emits a header and a JSON report;
`check` compares both without writing. Native methods require object roots.
An omitted method produces E701 and exit 1 unless `--allow-unsupported` is
explicit. Omissions stay in the report; the flag does not repair or widen them.
The current three omitted methods are `Lynx.getVersion`, `Replay.end` and
`Template.templateConfigInfo`. Event generation is not part of this native
method interface; events remain supported by the standalone contract/check/verify
pipeline. Keeping full catalog outputs outside the source tree also avoids
committing schemas from the paused local declaration work.

Compile the generated catalog against the pinned runtime:

```sh
cmake -S devtool/lynx_devtool/protocol/tests/cdpc_runtime \
  -B /tmp/cdpc-catalog-build -G Ninja \
  -DCDPC_SOURCE_DIR="$CDPC_SOURCE_DIR" -DCDPC_CONTRACT_DIR=/tmp/cdpc-catalog
cmake --build /tmp/cdpc-catalog-build
ctest --test-dir /tmp/cdpc-catalog-build --output-on-failure
```

This test validates every emitted method, cross-translation-unit storage,
recursive DOM results, Input validation, owned argument handoff, deferred reply,
strict results and duplicate completion. It does not call a real UI backend.
It is a standalone test project, with no production GN dependency or dispatch
change. Add the usual `-fsanitize=undefined -fno-sanitize-recover=all
-fno-omit-frame-pointer -fno-exceptions -fno-rtti` compiler flags and
`-fsanitize=undefined` linker flag to verify that build mode.

The checked-in CDPC Input/shared-case headers are stable fixtures. Regenerate
or compare them through `generate_fixtures.py --output-directory <cdpc>`;
`--check` verifies equality. Normal CDPC CMake builds use those headers directly
and do not import the Lynx Python adapter.

## Extraction parity

Before replacing an old tool, preserve its package directory, then compare both
versions against exactly the same working tree and environment:

```sh
# /tmp/legacy-cdpc must contain the original cdpc/ package, including local edits.
tools/env.sh python3 devtool/lynx_devtool/protocol/tests/cdpc_runtime/compare_extraction.py \
  --legacy-scripts /tmp/legacy-cdpc --report /tmp/cdpc-extraction.json
```

The comparison covers byte equality for the manifest, contract JSON and legacy
C++ parameter rules, plus exit codes and complete JSON output for check, dry-run
write, show and upstream diff. The evidence file records output hashes, the
consumer pin and the frozen legacy module hashes. Freeze the tool before editing;
using a different historical catalog or dropping uncommitted source changes does
not establish parity for the current checkout.

On 2026-10-22, all comparisons matched for 187 registered methods and 52 observed
events. Check remained at 0 errors and 4 registered-root warnings. The installed
standalone CLI also emitted byte-identical contract JSON from the exported
inventory. All 64 Lynx Python tests passed in the current working tree. An
archive of the committed consumer sources separately ran 60 tests: 59 passed
and the C++ recorder-trace test skipped because its local recording is not
versioned. The four additional parameter-guard tests belong to the paused
untracked work and were not included in these commits. The 184 emitted object-root methods
passed compilation and runtime validation in normal and UBSan builds; CDPC's ten
CTest groups passed in both modes with generated fixtures. These are source,
compile and local runtime results, not device/frontend evidence. The existing
paused CDPResponder, parameter guard and business changes remain untouched.

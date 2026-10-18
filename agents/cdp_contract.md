# CDP Contract Notes

CDP Contract makes `devtool/lynx_devtool/protocol/custom_cdp_docs/` the single
source of truth for Lynx DevTool CDP. Docs checks, the generated manifest, the
runtime contract checker and, for typed domains, the C++ types and dispatch
are all derived from it. Full field rules live in
`devtool/lynx_devtool/protocol/cdp_metadata_workflow.md` (Contract Rules,
Typed Codegen, Runtime Contract Check).

## Layers

1. Contract and checks: docs and example validation, `cdp_manifest.generated.yaml`
   (version 3), and `CDPContractChecker` on the DevTool message channels. Applies
   to every domain.
2. Generated types: plain structs plus `Decode` / `Encode` for domains marked
   `codegen: typed` in `cdp_support.yaml`.
3. Generated dispatch: `<Method>Responder` aliases, a `Backend` interface and a
   generic `Agent` with `CreateAgent`. Layer 3 must stay removable without
   touching layers 1 and 2.

WhiteBoard is the only typed domain so far.

## Files

Generated; never edit by hand, run `tools/cdpc write` instead:

- `protocol/cdp_manifest.generated.yaml`
- `protocol/cdp_protocol.generated.{h,cc}`: typed domains, linked into production.
- `protocol/testdata/fixture/fixture_protocol.generated.{h,cc}`: test-only
  output of the codegen fixture.
- `protocol/cdp_contract_schema.generated.{h,cc}`: runtime contract data for the
  checker. Test and debug targets only; never depend on it from production.

Hand-written:

- `protocol/custom_cdp_docs/<Domain>/{methods,events}/<name>.yaml`: the contract.
- `protocol/cdp_support.yaml`: `codegen: typed` and `deviations` from upstream.
- `protocol/scripts/cdpc/`: the `cdpc` package behind `tools/cdpc` and
  `tools/cdpc.ps1`. Find the module for a change in the cdpc Modules table of
  `cdp_metadata_workflow.md`; imports only go downward (`cdpc.LAYERS`).
- `base_devtool/native/public/{cdp_codec,cdp_typed_responder,cdp_traffic_observer}.h`
- `protocol/cdp_contract_checker.{h,cc}`

Paths above are relative to `devtool/lynx_devtool/` or `devtool/`.

## Commands

```bash
tools/cdpc write
tools/cdpc check --require-primjs
tools/cdpc list DOM --no-docs
tools/cdpc show DOM.enable
tools/cdpc stubs WhiteBoard --apply
tools/cdpc diff DOM
```

`tools/cdpc` wraps the scripts below; `tools/cdpc help` lists every command.
Use `show <Domain.name>` to read an entry's effective shape (upstream plus
deviations plus docs, with each field's source) instead of merging the three
files by hand, and `list` to find entries and the file that implements them.
`diff` lists unimplemented upstream methods, events found in
sources, and field-level differences (deviations, docs additions, and nested
docs fields the contract ignores). Check it before migrating a standard domain.

Run `tools/hab sync .` first. It provides the PrimJS sources and the
clang-format 18 binary that normalizes generated C++.

## Adding a command to a typed domain

1. Add `custom_cdp_docs/<Domain>/methods/<method>.yaml` with `summary`,
   `parameters`, `returns` and `examples`.
2. Run `tools/cdpc write`. Invalid examples are reported as `file:line: reason`;
   a Backend implementation that lacks the new method is pointed out.
3. The build now fails because `Backend` has a new pure virtual method.
4. Run `tools/cdpc stubs <Domain> --apply`. It adds the override and a stub
   that replies `MethodNotFound`; replace the stub body with the
   implementation. Test doubles implementing the Backend are updated by hand.
5. Cover success and error paths in the domain's unit tests, then run
   `tools/cdpc check --require-primjs`.

When documenting an entry that already shipped, set `since` in its YAML by
hand (a quoted string such as `'4.1'`) to the Lynx version that introduced it;
otherwise the manifest records the current version.

Do not add `functions_map_` entries or `if (method == ...)` branches to a typed
domain. Its method inventory comes from the contract, and leftover legacy
declarations fail the update script.

## Rules

- `CDPResponder` belongs to another owner. Build on its existing API and do not
  change its semantics from this work. Report problems found through the
  contract, such as the empty success sent when a responder is dropped, as
  collaboration points.
- `cdp::Responder<R>` only types the success result. Completion, the fallback
  response and threading stay with `CDPResponder`.
- Reject requests before decoding by overriding `Agent::PreDispatch`; it runs
  synchronously and must reply before returning `false`. A compile-time
  `RequestGuard` is deferred; discuss the design before adding one.
- Handlers decode where params enter (the generated Agent) and keep typed
  params down to the consumer. Do not decode and then convert back to
  `Json::Value`.
- Build results and events with the generated types and `Encode`. Do not
  hand-assemble `{"method": ..., "params": ...}` in typed domains.
- Every escape hatch lives in `cdp_support.yaml` with a `reason` and is checked
  by the update script. Shape differences from upstream are `deviations`, not
  C++ workarounds.
- Keep headers that many files include free of the generated header; forward
  declare delegates instead. One shape change should rebuild only the files of
  that domain.
- A standard domain becomes typed by listing the upstream `methods` (and
  `events`) Lynx implements in `cdp_support.yaml`; unlisted entries are not
  generated, and documented standard entries must be listed. Migrate the
  whole domain at once: leftover legacy dispatch fails the update script.
- Typed codegen does not yet support `root: true`. The generator fails on it;
  do not work around it in the docs. `$ref`,
  recursive types (held through `cdp::Box<T>`), and enums or unions inside
  arrays and maps are supported.
- Generator changes must be covered by the fixture in
  `protocol/testdata/fixture/`, which `cdp_protocol_fixture_unittest.cc`
  compiles. Extend its `protocol.json` and docs for new shapes.
- Commands that do not reply exactly once, or whose result is not an object,
  are not ready for typed codegen. Fix the protocol shape first.
- Engine-forwarded domains (Debugger, Runtime, Profiler, HeapProfiler) stay out
  of codegen.

## Verification

Unit test targets: `cdp_contract_checker_unittest_exec`,
`shared_data_unittest_exec`, `devtool_agent_unittest_exec` and
`devtool_base_unittest_exec`. Python tests are in `protocol/scripts/cdpc/*_test.py`.

The runtime checker only observes `ViewMessageChannel` and
`GlobalMessageChannel` traffic. Calls made through `InvokeCDPFromSDK` are not
covered.

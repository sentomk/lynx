# DevTool CDP contracts

Read this file before changing a CDP method, event, YAML contract, or `cdpc`.
The detailed workflow is in
[`cdp_metadata_workflow.md`](../devtool/lynx_devtool/protocol/cdp_metadata_workflow.md).

## Sources and ownership

- `devtool/lynx_devtool/protocol/custom_cdp_docs/<Domain>/methods/<name>.yaml`
  describes a method; `events/<name>.yaml` describes an event.
- `cdp_upstream/protocol.json` supplies standard CDP definitions. Local fields
  override upstream fields. `cdp_support.yaml` contains justified deviations and registered protocol violations.
- `scripts/cdpc/model.py` loads these into a single effective contract.
  `schema.py` owns field validation; `inventory.py` scans registrations.
- `tools/cdpc write` writes `cdp_manifest.generated.yaml` (version 2) and
  `cdp_contract.generated.json` (version 2). Do not edit these outputs by hand.
- Business code owns dispatch, implementations, responses, and event emission.
  Declaring an event does not make the application emit it.

The schema supports string (optionally enum), integer, number, boolean, object,
array, any, null, and references to upstream types. YAML unions use `string|null`;
the generated JSON uses an ordered type list. Object fields use `properties`,
and dynamic `<key>` path segments use `values`. Arrays must declare `items` or
nested item fields. A bare object checks only its type.

Params and returns normally have object roots. Known non-object roots require a
`non-object-root` violation with a reason in `cdp_support.yaml`; `check` warns
until the protocol is corrected. Keep those warnings visible in CI. Unsupported
entries are listed in the generated `unsupported.methods/events` arrays and
produce `kUnsupportedContract` in both checkers. `write` lists omissions and
exits 1; `--allow-unsupported` is an explicit temporary escape hatch.

## Commands

Run the wrapper directly; it sets up Python and the module path. Windows uses
`tools\cdpc.ps1`. `-h` / `--help` is available for every command.

```bash
tools/cdpc check [TARGET...] [--strict] [--format text|json]
tools/cdpc write [--dry-run] [--allow-unsupported] [--format text|json]
tools/cdpc show [TARGET] [--format text|json]
tools/cdpc new Domain.name [--event] [--from-upstream]
tools/cdpc verify TRACE... [TARGET...] [--format text|json]
tools/cdpc capture [TARGET...] --client ID --session ID [-o PATH] [--format text|json]
tools/cdpc upstream diff [DOMAIN] [--format text|json]
tools/cdpc upstream update [--revision REV] [--format text|json]
```

Targets are domains, full names, or quoted globs such as `'*.enable'`. Exit 0
means success, 1 means reported problems, and 2 means usage or environment
failure. JSON output is one object with `problems` and `summary`.

`check` checks declarations, examples, generated files, and method registrations.
Generated-file checks always cover the complete catalog, even with a target.
Missing PrimJS source is a warning, or an error with `--strict`.
`show` displays domains, matching entries, or one effective schema and its origin.
`new` creates an editable YAML template and refuses to overwrite a file.
`upstream diff` reports unimplemented standard methods, undeclared event names
found in sources, and field differences for deviations. It is source evidence,
not a runtime coverage measurement. `upstream update` fetches the requested
revision (default `master`) and regenerates both outputs.

## Changing a method or event

1. Use `new` for a new entry, then replace placeholders and add representative
   request/response or event examples. Preserve existing behavioral constraints.
2. Register and implement the method, or connect the event sender in business code.
3. Update the relevant Agent tests and use `ContractTestHarness` for traffic checks.
4. Run `write`, targeted `check`, and affected C++ tests. Before delivery, run
   `check --strict` and report any remaining unsupported declarations explicitly.

Request fields are checked with unknown fields allowed. Result and event fields
are strict. Error responses require an integer `code` and a string `message`,
with optional `data`. Examples returning InvalidParams or InvalidRequest may
intentionally contain invalid request fields; their responses are still checked.

## ContractTestHarness

The header is `devtool/lynx_devtool/protocol/contract_test_harness.h` and the GN
dependency is `//devtool/lynx_devtool/protocol:contract_test_harness` (`testonly`).

```cpp
ContractTestHarness harness;
auto sender = harness.Wrap(raw_sender);
Json::Value request(Json::objectValue);
request["id"] = 1;
request["method"] = "Lynx.getLogLevel";
harness.Dispatch(agent, sender, request);
```

Use the wrapped sender everywhere, including the sender returned to event
producers. Drain asynchronous TASM/UI/DevTool work before destroying the harness.
Use one harness per test; it installs the process-wide observer and restores the previous observer at
teardown. Unexpected violations and expected violations that never occur fail
the test. Violations are also printed immediately to stderr, including expected
violations, so a later handler crash does not hide them. For intentional malformed input, declare the exact expected kind/name:

```cpp
harness.Expect(ContractViolation::Kind::kInvalidParams, "Lynx.setLogLevel");
```

`kDropped` means a responder was destroyed without an explicit reply. It logs a
warning and retains the existing empty success fallback. `kUnanswered` means a
request was still pending at harness teardown. Do not suppress either to hide an
asynchronous lifetime issue.

The checker, embedded JSON action, and harness are test-only. `embed_contract.py`
uses only Python's standard library. Shared Python/C++ field cases live in
`scripts/cdpc/tests/testdata/contract_cases.json`.

## Desktop traffic recording

With `enable_inspector=true`, start the desktop Explorer executable with:

```bash
LYNX_CDP_TRACE=/tmp/explorer.trace.jsonl /path/to/LynxExplorer
# Connect the frontend and exercise the relevant panel, then exit Explorer.
tools/cdpc verify /tmp/explorer.trace.jsonl
```

The opt-in recorder serializes messages and writes them on a dedicated worker.
The path is opened for a new recording, and queued records drain on normal exit.
It never replaces an already installed observer. Messages above 1 MiB keep only
method/id metadata and `truncated: true`; offline checks preserve correlation and
skip their field validation. The queue holds at most 10,000 rows; excess rows
are dropped and counted in a final stats record. `verify` warns that such a trace
is incomplete. `verify` reads current YAML directly; no `write` is
needed. Recordings contain traffic payloads, so choose a suitable local path.

## Safe example capture

`tools/cdpc capture DOM CSS Page --client ID --session ID -o trace.jsonl`
uses the optional `agent-lynx` CLI and automatically verifies the resulting trace.
Only methods explicitly marked `capture: safe` in their YAML are replayed; the
default is `unsafe`. Review side effects before marking a method safe.
`$rootNodeId` in an example request is resolved via the safe `DOM.getDocument`
method. Static example checks use a typed sample node id. Output files must not
already exist. Requests are saved before sending, so failures retain a partial
trace. Structured CLI errors retain their CDP code and message; older CLI
versions that emit only text errors are reported as environment failures.

## Independent CDPC toolchain

The contract semantics now live in the independent CDPC repository as
`python/cdpc_core`. Lynx's `scripts/cdpc` retains path adapters, registration
scanning, the manifest and C++ bindings, snapshot update, capture and the consumer
CLI. `cdpc_toolchain.json` pins the exact dependency commit; set `CDPC_SOURCE_DIR`
to a clean matching checkout (default: a sibling `cdpc` directory). The wrapper
never downloads dependencies during a build. Changes to the independent core
must be committed and deliberately adopted by updating the pin.

Use `tools/cdpc runtime export --output-directory <dir>` to produce normalized
implementation evidence and an explicit standalone config. Use
`tools/cdpc runtime write --output-directory <dir>` for new native C++ method
descriptors. Native scalar roots are explicit omissions (E701); use
`--allow-unsupported` only when reviewing that omission report. Generation is not
production dispatch integration. The existing contract JSON remains v2, and the
new repository-independent descriptor graph is v1.

See [toolchain and generation verification](../devtool/lynx_devtool/protocol/tests/cdpc_runtime/README.md)
for standalone commands, parity checks and the C++ integration test. Stable
generated fixtures are checked into CDPC; full catalog outputs are currently
separate test artifacts while the Lynx business migration remains paused.

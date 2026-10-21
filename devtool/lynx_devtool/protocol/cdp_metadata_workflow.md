# CDP contract workflow

`cdpc` maintains a catalog shared by documentation checks, C++ unit tests, and
offline traffic verification. It writes metadata and contract JSON. Business code
continues to dispatch methods and emit events.

## Repository layout

| Path under this directory | Role |
|---|---|
| `custom_cdp_docs/<Domain>/methods/*.yaml` | Local method contracts and examples |
| `custom_cdp_docs/<Domain>/events/*.yaml` | Local event contracts and examples |
| `cdp_upstream/protocol.json` | Vendored standard protocol definitions |
| `cdp_support.yaml` | Explained field deviations from upstream |
| `cdp_manifest.generated.yaml` | Version 2 method registration inventory |
| `cdp_contract.generated.json` | Version 1 effective schemas and examples |
| `scripts/cdpc/` | Python model, checks, inventory, and CLI |
| `scripts/embed_contract.py` | Standard-library-only JSON embedding for GN |
| `contract_checker.{h,cc}` | Test-only field and response lifecycle checks |
| `contract_test_harness.{h,cc}` | Test-only Agent integration |

Use `tools/cdpc` from the repository root (`tools\cdpc.ps1` on Windows).
Python requires PyYAML. Sync dependencies with `tools/hab sync .` when PrimJS or
build tools are missing. `tools/cdpc --help` lists the six command groups.

## Schemas and local YAML

A method document contains a summary, parameter fields, return fields, examples,
and notes. An event has parameter fields and no return schema. For example:

```yaml
summary: Reads the current log thresholds.
parameters: []
returns:
  - name: runtimeLevel
    type: string
  - name: nativeCompileLevel
    type: string
examples:
  - title: Read thresholds
    request: {id: 1, method: Lynx.getLogLevel}
    response:
      id: 1
      result: {runtimeLevel: INFO, nativeCompileLevel: MONITOR}
notes: []
```

Supported types are `string`, `integer`, `number`, `boolean`, `object`, `array`,
`null`, and `any`; `$ref` names an upstream type. Unqualified references use the current
domain. Unions use `|` (for example `string|null`) and preserve type order. String enums are lists of distinct strings. Fields are required unless
`optional: true` is present. Arrays require `items` or nested item declarations.

```yaml
parameters:
  - name: changes
    type: array
  - name: changes[].operation
    type: string
    enum: [replace]
  - name: changes[].value
    type: object
```

Nested parents must be declared before their children. A bare `object` accepts
business-defined fields, while an object with declared properties checks them.
Requests allow unknown fields; results and event params reject unknown fields.
Absent or null object **params envelopes** are treated as an empty object.
Non-object roots require a `non-object-root` violation in `cdp_support.yaml`;
registered roots retain their actual JSON value and produce a warning. A stale
registration fails with E502. Dynamic `<key>` fields generate an object `values`
schema; fixed properties take precedence over dynamic keys.

Local field declarations override upstream declarations; unspecified upstream
fields remain. `cdp_support.yaml` applies `added`, `omitted`, or `retyped` field
deviations first. Each deviation requires a target and a reason. For example:

```yaml
Debugger:
  deviations:
    - target: paused.parameters.reason
      kind: retyped
      as: {type: string}
      reason: PrimJS also reports stopAtEntry.
```

The catalog includes local entries, registered upstream methods, upstream events
identified in sources/examples, and deviations, plus recursively referenced
types. It does not export the entire upstream protocol. Method and event entries
with the same full name remain separate.

Contract JSON version 2 represents unions as type lists, maps with `values`, and
registered non-object roots with `violation: non-object-root`. Whole unsupported
entries are recorded in `unsupported.methods/events` and omitted from the active
schemas. `write` names every omitted entry and exits 1, unless explicitly given
`--allow-unsupported`. Both C++ and offline verification report
`kUnsupportedContract`; unknown names still report `kUnknownMethod/Event`.

## CLI

All commands support `--format text|json`; for upstream, put it after `diff` or
`update`. Exit codes are 0 (success), 1 (problems), and 2 (usage/environment).
Text diagnostics use `file:line: code: message`. JSON output has `problems` and
`summary`; trace problems also contain `id` and a list of field problems.

```bash
tools/cdpc check --strict
tools/cdpc check Lynx 'WhiteBoard.*'
tools/cdpc write --dry-run
tools/cdpc write
tools/cdpc show
tools/cdpc show GlobalProps
tools/cdpc show GlobalProps.changed --format json
tools/cdpc new Lynx.getFoo
tools/cdpc new Lynx.fooChanged --event
tools/cdpc new DOM.getDocument --from-upstream
tools/cdpc upstream diff DOM
tools/cdpc upstream update --revision <devtools-protocol-commit>
```

`new` refuses to overwrite existing files. Replace its TODO placeholders, add
examples, and register/implement the method or event sender before running
`write` and `check`. `--from-upstream` copies upstream field declarations.
`upstream update` defaults to `master` if no revision is specified; use an exact
revision for repeatable updates. It invokes `scripts/update_cdp_upstream.py`
then regenerates metadata. Both upstream documents must be valid before replacing
the snapshot. Upstream comparison reports three groups: unimplemented standard
methods, undeclared event names found in sources, and field-level deviations.

| Codes | What is checked |
|---|---|
| E101–E103 | YAML structure, supported field types, references |
| E201–E202 | Example envelopes and field values |
| E301 | Missing or stale generated files |
| E401–E403 | Missing contracts, unregistered local methods, missing PrimJS source |
| E501–E502 | Invalid or redundant deviations |

Targets accept domains, full names, and shell-quoted globs. Generated-file checks
always cover all entries. Missing PrimJS source is a warning unless `--strict`
is set. Source event scanning identifies string literals; it does not prove that
those paths execute. Keep runtime coverage distinct from the registration catalog.

## C++ tests

Depend on `//devtool/lynx_devtool/protocol:contract_test_harness` and include
`devtool/lynx_devtool/protocol/contract_test_harness.h`. Construct a harness before
senders and asynchronous owners, use `Wrap()` for every response/event sender,
and route requests through `Dispatch(agent, wrapped_sender, request)`.

For mediator events, also replace the sender returned by the DevTool instance.
Drain queued TASM, UI, and DevTool tasks before harness destruction. The harness
installs a process-wide observer, so use one per test. Its destructor flushes
pending requests and fails on unexpected violations. `Expect(kind, name)` consumes
one matching violation and fails if it never occurs. Use it only for intentionally
invalid traffic; an agent may reject values more narrowly than its YAML type.

The violation kinds are `kInvalidParams`, `kInvalidResult`, `kInvalidError`,
`kInvalidEvent`, `kUnknownMethod`, `kUnknownEvent`, `kDuplicateResponse`, `kDropped`,
and `kUnanswered`. A dropped responder is reported before its unchanged InternalError
fallback; that fallback is not checked again for result fields. A second response
is still a duplicate. Requests with the same id on different channels are distinct.

The checker parses embedded JSON once, resolves type references, and bounds its
pending table. No checker or contract JSON is linked into SDK targets. Rebuild
after `tools/cdpc write` to consume contract changes. Documentation examples are
message fragments: output fields are checked, but they do not imply a full
request lifecycle. Examples of InvalidParams/InvalidRequest may send invalid
request fields intentionally.

## Recording and offline verification

The recorder is compiled when `enable_inspector=true`. DevTool initialization
reads `LYNX_CDP_TRACE` once; an absent variable does nothing. If an observer is
already installed, it logs a warning and leaves it in place.

```bash
LYNX_CDP_TRACE=/tmp/explorer.trace.jsonl /path/to/LynxExplorer
# Connect the frontend, open Elements, perform the scenario, then exit.
tools/cdpc verify /tmp/explorer.trace.jsonl
tools/cdpc verify /tmp/one.jsonl /tmp/two.jsonl 'DOM.*' --format json
cat /tmp/explorer.trace.jsonl | tools/cdpc verify - Lynx
```

Each JSONL row contains a millisecond timestamp `t`, a channel string, and a `dir`
of `in`, `out`, or `dropped`. In/out rows carry `message`; dropped rows carry `id`.
Messages over 1 MiB retain method/id metadata and top-level `truncated: true`.
The verifier still tracks their lifecycle but skips field validation. Each file
is a separate session and flushes unanswered requests at EOF. It reads current
YAML directly, independent of generated files.

Callbacks serialize/enqueue only. A dedicated worker writes and flushes lines.
Opening a trace path starts a new recording; normal process exit drains queued
lines. Forced termination can lose queued data. Mobile recording controls are
outside this workflow.

## Validation commands

```bash
tools/env.sh python3 -m unittest discover \
  -s devtool/lynx_devtool/protocol/scripts/cdpc/tests \
  -t devtool/lynx_devtool/protocol/scripts -p '*_test.py'
tools/env.sh ninja -C out/cdpc devtool_base_unittest_exec \
  devtool_agent_unittest_exec shared_data_unittest_exec contract_checker_unittest_exec
out/cdpc/devtool_base_unittest_exec
out/cdpc/devtool_agent_unittest_exec
out/cdpc/shared_data_unittest_exec
out/cdpc/contract_checker_unittest_exec
tools/cdpc check --strict
```

Use the explicit `*_test.py` discovery pattern: the default pattern matches none
of this package's tests. The recorder C++ test writes an ignored
`scripts/cdpc/tests/testdata/recorder.trace.jsonl`. Run it before Python tests for
the direct C++→Python format check; otherwise that one integration test is skipped.
The fixture intentionally contains one dropped response, and `verify` must exit 1
with exactly one `kDropped`. This is separate from a manual Explorer/frontend run.

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

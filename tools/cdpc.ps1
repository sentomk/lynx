# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

# cdpc: Lynx DevTool CDP Contract command line. Run `tools\cdpc.ps1 --help`.

$LynxRoot = Split-Path -Parent $PSScriptRoot
$Scripts = Join-Path $LynxRoot "devtool\lynx_devtool\protocol\scripts"
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$Scripts;$env:PYTHONPATH" } else { $Scripts }
& (Join-Path $PSScriptRoot "env.ps1") python -m cdpc_toolchain @args
exit $LASTEXITCODE

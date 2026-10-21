# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Lynx snapshot update command and shared upstream comparison."""
import subprocess
import sys
from cdpc.model import Loader
from cdpc_core.upstream import field_at, diff


def update(repo, revision=None):
    script = repo.protocol / 'scripts/update_cdp_upstream.py'
    result = subprocess.run([sys.executable, str(script), '--write', '--revision', revision or 'master'],
                            cwd=repo.root, text=True, capture_output=True)
    if result.returncode:
        raise OSError('upstream update failed: ' + result.stderr.strip())
    return result.stdout.strip()

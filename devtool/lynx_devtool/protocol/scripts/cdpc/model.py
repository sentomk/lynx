# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Lynx source locations; model semantics live in the pinned CDPC package."""
from dataclasses import dataclass
from pathlib import Path
from cdpc_toolchain import activate

activate()
from cdpc_core.model import (  # noqa: E402,F401
    Inputs, Diagnostic, Entry, Contract, Loader, entry_key, matches,
    field_origins, read_yaml, apply_deviation, load as _load,
)


@dataclass(frozen=True)
class Repo:
    root: Path

    @classmethod
    def current(cls):
        return cls(Path(__file__).resolve().parents[5])

    @property
    def protocol(self):
        return self.root / 'devtool/lynx_devtool/protocol'

    @property
    def docs(self):
        return self.protocol / 'custom_cdp_docs'

    @property
    def upstream(self):
        return self.protocol / 'cdp_upstream/protocol.json'

    @property
    def support(self):
        return self.protocol / 'cdp_support.yaml'

    @property
    def manifest(self):
        return self.protocol / 'cdp_manifest.generated.yaml'

    def relative(self, path):
        return Path(path).relative_to(self.root).as_posix()


def load(repo):
    return _load(Inputs(repo.root, repo.docs, repo.upstream, repo.support))

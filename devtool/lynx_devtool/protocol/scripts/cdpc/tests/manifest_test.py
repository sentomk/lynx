# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Tests for manifest.py."""

import tempfile
import unittest
from pathlib import Path

from cdpc import contract, manifest, scan
from cdpc.repo import CdpcError
from cdpc.tests.support import TempRepoTestCase


class ManifestTest(TempRepoTestCase):
    def test_load_existing_manifest_since(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_file = Path(temp_dir) / "cdp_manifest.generated.yaml"
            manifest_file.write_text(
                "\n".join(
                    [
                        "domains:",
                        "  - name: DOM",
                        "    origin: standard",
                        "    scope: instance",
                        "    methods:",
                        "      - name: getDocument",
                        "        origin: standard",
                        '        since: "3.2"',
                        "        source: dom.cc",
                        "    events:",
                        "      - name: documentUpdated",
                        "        origin: standard",
                        "        since: '3.3'",
                        "        source: dom.cc",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            existing_manifest = manifest.load_existing_manifest(manifest_file)

            self.assertIn("DOM", existing_manifest.domains)
            self.assertIn(("DOM", "getDocument"), existing_manifest.methods)
            self.assertIn(("DOM", "documentUpdated"), existing_manifest.events)
            self.assertEqual(
                existing_manifest.method_since[("DOM", "getDocument")], "3.2"
            )
            self.assertEqual(
                existing_manifest.event_since[("DOM", "documentUpdated")], "3.3"
            )

    def test_build_manifest_assigns_since_for_method_entries(self):
        existing_manifest = manifest.ExistingManifest(
            domains={"Component", "DOM"},
            methods={
                ("Component", "uselessUpdate"),
                ("DOM", "getDocument"),
                ("DOM", "getDocumentWithBoxModel"),
            },
            events=set(),
            method_since={
                ("Component", "uselessUpdate"): "3.8",
                ("DOM", "getDocument"): "3.8",
                ("DOM", "getDocumentWithBoxModel"): "3.2",
            },
            event_since={},
        )
        text = manifest.build_manifest(
            paths=None,
            local_methods=[
                scan.LocalMethod(
                    domain="Component", method="uselessUpdate", source="component.cc"
                ),
                scan.LocalMethod(
                    domain="DOM", method="getDocument", source="dom.cc"
                ),
                scan.LocalMethod(
                    domain="DOM", method="getDocumentWithBoxModel", source="dom.cc"
                ),
                scan.LocalMethod(
                    domain="DOM", method="newMethod", source="dom.cc"
                ),
                scan.LocalMethod(
                    domain="Lynx", method="getData", source="lynx.cc"
                ),
            ],
            scopes={
                "Component": "global-and-instance",
                "DOM": "instance",
                "Lynx": "instance",
            },
            upstream_methods={"DOM": {"getDocument"}},
            existing_manifest=existing_manifest,
            current_lynx_version="4.2",
        )

        self.assertIn(
            "\n  - name: Component\n"
            "    origin: lynx-extension\n"
            "    scope: global-and-instance\n",
            text,
        )
        self.assertIn(
            "\n  - name: DOM\n    origin: standard\n    scope: instance\n",
            text,
        )
        self.assertIn(
            "\n      - name: getDocument\n        origin: standard\n"
            "        since: '3.8'\n"
            "        source: dom.cc\n",
            text,
        )
        self.assertIn(
            "\n      - name: getDocumentWithBoxModel\n"
            "        origin: lynx-extension\n"
            "        since: '3.2'\n"
            "        source: dom.cc\n",
            text,
        )
        self.assertIn(
            "\n      - name: newMethod\n"
            "        origin: lynx-extension\n"
            "        since: '4.2'\n"
            "        source: dom.cc\n",
            text,
        )
        self.assertIn(
            "\n  - name: Lynx\n"
            "    origin: lynx-extension\n"
            "    scope: instance\n",
            text,
        )

    def test_build_manifest_rejects_existing_entry_without_since(self):
        existing_manifest = manifest.ExistingManifest(
            domains={"DOM"},
            methods={("DOM", "getDocument")},
            events=set(),
            method_since={},
            event_since={},
        )

        with self.assertRaisesRegex(
            CdpcError,
            r"existing manifest entry DOM\.getDocument is missing mandatory `since`",
        ):
            manifest.build_manifest(
                paths=None,
                local_methods=[
                    scan.LocalMethod(
                        domain="DOM", method="getDocument", source="dom.cc"
                    )
                ],
                scopes={"DOM": "instance"},
                upstream_methods={"DOM": {"getDocument"}},
                existing_manifest=existing_manifest,
                current_lynx_version="4.2",
            )

    def test_entry_since_prefers_declared_then_history_then_current(self):
        key = ("Board", "changed")
        self.assertEqual(
            manifest.entry_since(key, {key}, {key: "4.4"}, "4.5", "4.1"),
            "4.1",
        )
        self.assertEqual(
            manifest.entry_since(key, {key}, {key: "4.4"}, "4.5"), "4.4"
        )
        self.assertEqual(manifest.entry_since(key, set(), {}, "4.5"), "4.5")

    def test_build_manifest_v3_events_lifecycle_and_deviations(self):
        def entry(kind, domain, name, **kwargs):
            return contract.ContractEntry(
                kind=kind,
                domain=domain,
                name=name,
                source=self.paths.source_root / f"docs/{domain}/{name}.yaml",
                origin=kwargs.pop("origin", "lynx-extension"),
                parameters=contract.Schema(),
                returns=None,
                **kwargs,
            )

        contracts = contract.ContractSet(
            methods={("Board", "set"): entry("method", "Board", "set", experimental=True)},
            events={
                ("Board", "changed"): entry("event", "Board", "changed", deprecated=True),
                ("Board", "added"): entry("event", "Board", "added"),
            },
            deviations=[
                contract.Deviation(
                    domain="Tracing",
                    target="tracingComplete.parameters.flag",
                    kind="added",
                    as_spec={"type": "boolean", "optional": True},
                    reason="Lynx flag.",
                )
            ],
            errors=[],
            warnings=[],
        )
        existing_manifest = manifest.ExistingManifest(
            domains={"Board"},
            methods={("Board", "set")},
            events={("Board", "added")},
            method_since={("Board", "set"): "4.1"},
            event_since={("Board", "added"): "4.2"},
        )
        text = manifest.build_manifest(
            paths=self.paths,
            local_methods=[
                scan.LocalMethod("Board", "set", "board.cc"),
                scan.LocalMethod("Tracing", "start", "tracing.cc"),
            ],
            scopes={"Board": "global", "Tracing": "global"},
            upstream_methods={"Tracing": {"start"}},
            existing_manifest=existing_manifest,
            current_lynx_version="4.4",
            contracts=contracts,
        )

        self.assertIn("\nversion: 3\n", text)
        self.assertIn(
            "      - name: set\n"
            "        origin: lynx-extension\n"
            "        experimental: true\n"
            "        since: '4.1'\n"
            "        source: board.cc\n"
            "    events:\n"
            "      - name: added\n"
            "        origin: lynx-extension\n"
            "        since: '4.2'\n"
            "        source: docs/Board/added.yaml\n"
            "      - name: changed\n"
            "        origin: lynx-extension\n"
            "        deprecated: true\n"
            "        since: '4.4'\n"
            "        source: docs/Board/changed.yaml\n",
            text,
        )
        self.assertIn(
            "    deviations:\n"
            "      - target: tracingComplete.parameters.flag\n"
            "        kind: added\n"
            "        as:\n"
            "          type: boolean\n"
            "          optional: true\n"
            '        reason: "Lynx flag."\n',
            text,
        )

        reloaded = Path(self.paths.source_root) / "manifest.yaml"
        reloaded.write_text(text, encoding="utf-8")
        parsed = manifest.load_existing_manifest(reloaded)
        self.assertEqual(parsed.event_since[("Board", "changed")], "4.4")
        self.assertEqual(parsed.method_source[("Board", "set")], "board.cc")


if __name__ == "__main__":
    unittest.main()

# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Tests for inventory.py."""

import unittest

from cdpc import contract, inventory, scan
from cdpc.repo import CdpcError
from cdpc.tests.support import TempRepoTestCase


class InventoryTest(TempRepoTestCase):
    def test_typed_domains_take_methods_from_contracts(self):
        contracts = contract.ContractSet(
            methods={
                ("Board", "set"): contract.ContractEntry(
                    kind="method",
                    domain="Board",
                    name="set",
                    source=self.paths.source_root / "docs/Board/methods/set.yaml",
                    origin="lynx-extension",
                    parameters=contract.Schema(),
                    returns=None,
                )
            },
            events={},
            deviations=[],
            errors=[],
            warnings=[],
            typed_domains={"Board"},
        )
        legacy = scan.LocalMethod("Other", "run", "other.cc")
        self.assertEqual(
            inventory.add_typed_methods(self.paths, [legacy], contracts),
            [
                scan.LocalMethod(
                    "Board", "set", "docs/Board/methods/set.yaml"
                ),
                legacy,
            ],
        )
        with self.assertRaisesRegex(
            CdpcError, r"Board\.old in board\.cc"
        ):
            inventory.add_typed_methods(
                self.paths,
                [scan.LocalMethod("Board", "old", "board.cc")],
                contracts,
            )

    def test_split_typed_leftovers(self):
        contracts = contract.ContractSet(
            methods={}, events={}, deviations=[], errors=[], warnings=[],
            typed_domains={"Board"},
        )
        legacy = scan.LocalMethod("Board", "old", "board.cc")
        other = scan.LocalMethod("Other", "run", "other.cc")
        self.assertEqual(
            inventory.split_typed_leftovers([legacy, other], contracts),
            ([other], [legacy]),
        )
        self.assertIn(
            "Board.old in board.cc",
            inventory.typed_leftovers_message([legacy]),
        )
        contracts.domain_legacy["Board"] = contract.DomainLegacy(
            agent="devtool/board_agent.cc", order=["old"], migrated=[]
        )
        in_agent = scan.LocalMethod("Board", "old", "devtool/board_agent.cc")
        elsewhere = scan.LocalMethod("Board", "old", "devtool/other.cc")
        errors, warnings = inventory.partition_leftovers([in_agent, elsewhere], contracts)
        self.assertEqual(warnings, [in_agent])
        self.assertEqual(errors, [elsewhere])

    def test_standard_typed_domain_methods_point_at_their_contract(self):
        upstream = contract.Upstream(
            domains={"DOM"},
            commands={
                ("DOM", "getDocument"): contract.UpstreamEntry(
                    contract.Schema(), contract.Schema()
                )
            },
            events={},
            types={},
        )
        contracts = contract.ContractSet(
            methods={},
            events={},
            deviations=[],
            errors=[],
            warnings=[],
            upstream=upstream,
            typed_domains={"DOM"},
            typed_upstream={"DOM": (["getDocument"], [])},
            support_file=self.paths.source_root / "cdp_support.yaml",
        )
        self.assertEqual(
            inventory.add_typed_methods(self.paths, [], contracts),
            [scan.LocalMethod("DOM", "getDocument", "cdp_support.yaml")],
        )


if __name__ == "__main__":
    unittest.main()

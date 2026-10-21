# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.
import json
from cdpc import emit, inventory, model, param_rules
from cdpc.tests.support import RepoTest


class ParamRulesTest(RepoTest):
    def load(self):
        contract, _ = model.load(self.repo)
        sources = inventory.scan(self.repo, contract)
        return inventory.complete(contract, sources), sources

    def test_only_registered_parameters_are_emitted(self):
        self.doc(parameters=[{'name': 'input', 'type': 'string|null'}],
                 returns=[{'name': 'unusedResult', 'type': 'integer'}])
        self.doc('Sample.unused', parameters=[{'name': 'unregistered', 'type': 'boolean'}])
        contract, sources = self.load()
        generated = param_rules.generate(contract, sources)
        self.assertIn('CDPParamType::kString | CDPParamType::kNull', generated)
        self.assertIn('"Sample.get"', generated)
        self.assertNotIn('unusedResult', generated)
        self.assertNotIn('unregistered', generated)
        self.assertNotIn('Sample.unused', generated)

    def test_recursive_refs_maps_and_arrays(self):
        upstream = json.loads(self.repo.upstream.read_text())
        upstream['domains'][0]['commands'][0]['parameters'] = [
            {'name': 'nodes', 'type': 'array', 'items': {'$ref': 'Node'}}]
        self.repo.upstream.write_text(json.dumps(upstream))
        contract, sources = self.load()
        generated = param_rules.generate(contract, sources)
        self.assertIn('"next"', generated)
        self.assertIn('CDPParamType::kArray', generated)
        self.assertLess(len(generated), 10000)  # recursion emits references
        self.doc(parameters=[{'name': 'labels', 'type': 'object',
                              'values': {'type': 'integer'}}])
        contract, sources = self.load()
        generated = param_rules.generate(contract, sources)
        self.assertIn('CDPParamType::kMap', generated)
        self.assertIn('CDPParamType::kInteger', generated)

    def test_yaml_change_is_stale_until_write(self):
        self.doc(parameters=[{'name': 'input', 'type': 'integer'}])
        contract, sources = self.load()
        emit.write(emit.generate(self.repo, contract, sources))
        self.doc(parameters=[{'name': 'input', 'type': 'boolean'}])
        contract, sources = self.load()
        outputs = emit.generate(self.repo, contract, sources)
        path = self.repo.root / 'devtool/base_devtool/native/cdp_param_rules.generated.cc'
        problems = emit.check(self.repo, outputs)
        self.assertTrue(any(p.code == 'E301' and p.file == self.repo.relative(path) for p in problems))
        self.assertNotIn('CDPParamType::kBoolean', path.read_text())
        emit.write(outputs)
        self.assertIn('CDPParamType::kBoolean', path.read_text())
        self.assertFalse(emit.check(self.repo, outputs))

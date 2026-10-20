# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.
import json
import yaml
from cdpc import model, inventory
from cdpc.tests.support import RepoTest

class ModelTest(RepoTest):
    def test_local_overrides_upstream_and_retains_lines(self):
        path=self.doc(returns=[{'name':'node','type':'string'}])
        contract, problems=model.load(self.repo)
        self.assertFalse(problems)
        entry=contract.get('method','Sample.get')
        self.assertEqual(entry.origin,'both')
        self.assertEqual(entry.returns['properties']['node']['schema'],{'type':'string'})
        self.assertGreater(entry.line_of['returns[0].type'],1)
        self.assertEqual(entry.source,path)

    def test_nested_array_paths(self):
        self.doc(kind='event',name='Sample.custom',parameters=[{'name':'changes','type':'array'},{'name':'changes[].operation','type':'string','enum':['replace']}])
        contract, problems=model.load(self.repo)
        self.assertFalse(problems)
        self.assertEqual(contract.get('event','Sample.custom').params['properties']['changes']['schema']['items']['properties']['operation']['schema']['enum'],['replace'])
        self.assertEqual(contract.get('event','Sample.custom').fields_from['params.changes[].operation'], 'local')

    def test_scalar_array_item_path(self):
        self.doc(parameters=[{'name':'items','type':'array'},{'name':'items[]','type':'string'}])
        contract, problems=model.load(self.repo)
        self.assertFalse(problems)
        self.assertEqual(contract.get('method','Sample.get').params['properties']['items']['schema'],{'type':'array','items':{'type':'string'}})

    def test_deviations_added_and_retyped(self):
        self.repo.support.write_text(yaml.safe_dump({'Sample':{'deviations':[
            {'target':'changed.parameters.reason','kind':'retyped','as':{'type':'string'},'reason':'custom reasons'},
            {'target':'changed.parameters.extra','kind':'added','as':{'type':'integer','optional':True},'reason':'local extra'}]}}))
        contract, problems=model.load(self.repo)
        self.assertFalse(problems)
        props=contract.get('event','Sample.changed').params['properties']
        self.assertNotIn('enum',props['reason']['schema'])
        self.assertTrue(props['extra']['optional'])

    def test_unsupported_types_do_not_become_exportable_any(self):
        self.doc(returns=[{'name':'value','type':'string|integer'}])
        contract, problems=model.load(self.repo)
        self.assertEqual(problems[0].code,'E102')
        self.assertFalse(contract.get('method','Sample.get').supported)

    def test_malformed_schema_and_recursive_yaml_are_diagnostics(self):
        self.doc(parameters=[{'name': 'bad', 'type': ['string', 'integer']}])
        contract, problems = model.load(self.repo)
        self.assertEqual(problems[0].code, 'E102')
        self.assertFalse(contract.get('method', 'Sample.get').supported)
        path = self.doc()
        path.write_text('summary: recursive\nparameters: &cycle [*cycle]\n')
        _, problems = model.load(self.repo)
        self.assertEqual(problems[0].code, 'E101')

    def test_registration_only_entries_resolve_refs_and_reject_alias_cycles(self):
        raw = json.loads(self.repo.upstream.read_text())
        raw['domains'][0]['types'] = [{'id': 'Node', '$ref': 'Other'},
                                      {'id': 'Other', '$ref': 'Node'}]
        self.repo.upstream.write_text(json.dumps(raw))
        contract, problems = model.load(self.repo)
        contract = inventory.complete(contract, inventory.scan(self.repo, contract))
        self.assertTrue(any(p.code == 'E103' and 'cycle' in p.message for p in problems))
        self.assertFalse(contract.get('method', 'Sample.get').supported)

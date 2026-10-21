# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.
import json
import yaml
from cdpc import model, inventory
from cdpc.tests.support import RepoTest

class ModelTest(RepoTest):
    def test_union_map_paths_and_reference_closure(self):
        self.doc(returns=[{'name': 'data', 'type': 'object|null'},
                          {'name': 'data.fixed', 'type': 'string'},
                          {'name': 'data.<key>.node', '$ref': 'Node'}])
        contract, problems = model.load(self.repo)
        self.assertFalse(problems)
        schema = contract.get('method', 'Sample.get').returns['properties']['data']['schema']
        self.assertEqual(schema['type'], ['object', 'null'])
        self.assertEqual(schema['values']['properties']['node']['schema'], {'ref': 'Sample.Node'})
        self.assertEqual(schema['properties']['fixed']['schema'], {'type': 'string'})

    def test_reject_duplicate_union_and_unregistered_root(self):
        for spec in ({'name': 'x', 'type': 'string|string'},
                     {'name': 'result', 'type': 'string', 'root': True}):
            with self.subTest(spec=spec):
                self.doc(returns=[spec])
                contract, problems = model.load(self.repo)
                self.assertTrue(any(p.code == 'E102' for p in problems))
                self.assertFalse(contract.get('method', 'Sample.get').supported)

    def test_registered_root_overrides_upstream_and_stale_violation_fails(self):
        self.repo.support.write_text(yaml.safe_dump({'Sample': {'violations': [
            {'target': 'get.returns', 'kind': 'non-object-root', 'reason': 'legacy value'}]}}))
        self.doc(returns=[{'name': 'result', 'type': 'string', 'root': True}])
        contract, problems = model.load(self.repo)
        self.assertEqual([(p.code, p.severity) for p in problems], [('W501', 'warning')])
        self.assertEqual(contract.get('method', 'Sample.get').returns,
                         {'type': 'string', 'violation': 'non-object-root'})
        self.doc(returns=[{'name': 'result', 'type': 'object', 'root': True}])
        _, problems = model.load(self.repo)
        self.assertEqual([p.code for p in problems], ['E502'])

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
        self.doc(returns=[{'name':'value','type':'string|missing'}])
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

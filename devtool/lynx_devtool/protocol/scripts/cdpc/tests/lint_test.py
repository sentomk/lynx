# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.
import yaml
from cdpc import lint, model
from cdpc.tests.support import RepoTest

class LintTest(RepoTest):
    def errors(self):
        contract,_=model.load(self.repo)
        return lint.check(self.repo,contract)

    def test_required_structure_and_placeholders(self):
        self.doc(summary='TODO: fill in')
        self.assertIn('E101',[p.code for p in self.errors()])
        self.doc()
        self.assertFalse(self.errors())

    def test_schema_errors_and_reference_resolution(self):
        for field, code in [({'name':'a','type':'bad'},'E102'),({'name':'a','type':'string','optional':'yes'},'E102'),({'name':'a','type':'string','enum':[2]},'E102'),({'name':'a','$ref':'Missing'},'E103')]:
            with self.subTest(field=field):
                self.doc(parameters=[field])
                self.assertIn(code,[p.code for p in self.errors()])
        self.doc(parameters=[{'name':'a','$ref':'Node','optional':True}])
        self.assertFalse(self.errors())

    def test_example_envelope_shape_and_error_examples(self):
        example={'title':'Read','request':{'id':1,'method':'Sample.get'},'response':{'id':1,'result':{'node':{'label':'ok'}}}}
        self.doc(examples=[example])
        self.assertFalse(self.errors())
        example['response']['result']['node']['label']=4
        self.doc(examples=[example])
        self.assertIn('E202',[p.code for p in self.errors()])
        example['response']={'result':{}}
        self.doc(examples=[example])
        self.assertIn('E201',[p.code for p in self.errors()])
        example['response']={'id':1,'error':{'code':-32602,'message':'missing value'}}
        self.doc(parameters=[{'name':'value','type':'string'}],examples=[example])
        self.assertFalse(self.errors())

    def test_deviations_validate_targets_and_redundancy(self):
        change={'target':'changed.parameters.reason','kind':'retyped','as':{'type':'string'},'reason':'local'}
        def write(): self.repo.support.write_text(yaml.safe_dump({'Sample':{'deviations':[change]}}))
        write(); self.assertFalse(self.errors())
        change['as']['enum']=['one']; write()
        self.assertIn('E502',[p.code for p in self.errors()])
        change['target']='changed.parameters.missing'; write()
        self.assertIn('E501',[p.code for p in self.errors()])

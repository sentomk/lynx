# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.
import json
from pathlib import Path
import unittest
from cdpc.schema import check

DATA = Path(__file__).parent/'testdata'
class SchemaTest(unittest.TestCase):
    def test_types(self):
        for kind, good, bad in [('string','a',1),('integer',2,True),('number',1,'1'),('boolean',False,0),('object',{},[]),('array',[],{}),('any',None,None)]:
            schema={'type':kind}
            if kind=='array': schema['items']={'type':'string'}
            with self.subTest(kind=kind):
                self.assertEqual(check(schema,good,strict=True,types={}),[])
                if kind!='any': self.assertTrue(check(schema,bad,strict=True,types={}))
        self.assertFalse(check({'type':'integer'},1.0,strict=True,types={}))
        self.assertTrue(check({'type':'integer'},1.5,strict=True,types={}))
        self.assertTrue(check({'type':'integer'},2**64,strict=True,types={}))

    def test_optional_ref_enum_and_strictness(self):
        types={'Choice':{'type':'string','enum':['a','b']}}
        schema={'type':'object','properties':{'choice':{'schema':{'ref':'Choice'},'optional':True}}}
        self.assertFalse(check(schema,{'extra':1},strict=False,types=types))
        self.assertEqual([str(p) for p in check(schema,{'choice':'c'},strict=True,types=types,path='params')],["params.choice: 'c' is not an allowed value"])
        self.assertEqual([str(p) for p in check(schema,{'extra':1},strict=True,types=types,path='params')],['params.extra: unexpected field'])
        self.assertTrue(check({'ref':'Missing'},None,strict=True,types=types))

    def test_shared_contract_cases(self):
        data=json.loads((DATA/'case_contract.json').read_text())
        for case in json.loads((DATA/'contract_cases.json').read_text()):
            with self.subTest(case=case['name']):
                event=case['direction']=='event'
                schema=data['events' if event else 'methods'][case['schema_entry']]['params' if event or case['direction']=='params' else 'returns']
                result=check(schema,case['value'],strict=case['direction']!='params',types=data['types'],path='params' if event else case['direction'])
                self.assertEqual([str(p) for p in result],case['problems'])

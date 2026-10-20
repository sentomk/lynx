# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.
import json
import yaml
from cdpc import emit, inventory, model
from cdpc.tests.support import RepoTest

class EmitTest(RepoTest):
    def test_export_only_selected_entries_and_recursive_types(self):
        contract,_=model.load(self.repo)
        sources=inventory.scan(self.repo,contract)
        contract=inventory.complete(contract,sources)
        data=emit.contract_data(contract)
        self.assertEqual(set(data['methods']),{'Sample.get'})
        self.assertEqual(set(data['types']),{'Sample.Node'})
        outputs=emit.generate(self.repo,contract,sources)
        text=outputs[self.repo.protocol/'cdp_contract.generated.json']
        self.assertEqual(text,json.dumps(json.loads(text),indent=2,sort_keys=True,ensure_ascii=False)+'\n')
        self.assertEqual(yaml.safe_load(outputs[self.repo.manifest])['version'],2)
        self.assertTrue(emit.check(self.repo,outputs))
        emit.write(outputs)
        self.assertFalse(emit.check(self.repo,outputs))

    def test_unsupported_entry_is_omitted_not_widened(self):
        self.doc(returns=[{'name':'bad','type':'integer|string'}])
        contract,_=model.load(self.repo)
        self.assertNotIn('Sample.get',emit.contract_data(contract)['methods'])

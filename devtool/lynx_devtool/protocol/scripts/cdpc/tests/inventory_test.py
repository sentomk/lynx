# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.
from cdpc import inventory, model
from cdpc.tests.support import RepoTest

class InventoryTest(RepoTest):
    def test_registered_upstream_and_missing_contract(self):
        contract,_=model.load(self.repo)
        sources=inventory.scan(self.repo,contract)
        contract=inventory.complete(contract,sources)
        self.assertFalse(inventory.check(self.repo,contract,sources))
        agent=self.repo.root/'devtool/lynx_devtool/agent/domain_agent/sample.cc'
        agent.write_text('functions_map_["Sample.missing"] = &Agent::Missing;')
        sources=inventory.scan(self.repo,contract)
        self.assertIn('E401',[p.code for p in inventory.check(self.repo,contract,sources)])

    def test_unregistered_local(self):
        self.doc(name='Sample.local')
        contract,_=model.load(self.repo)
        sources=inventory.scan(self.repo,contract)
        self.assertIn('E402',[p.code for p in inventory.check(self.repo,inventory.complete(contract,sources),sources)])

    def test_missing_primjs_warning_and_strict(self):
        (self.repo.root/'third_party/quickjs/src/src/inspector/protocols.cc').unlink()
        contract,_=model.load(self.repo)
        for strict,severity in [(False,'warning'),(True,'error')]:
            self.assertEqual(inventory.scan(self.repo,contract,strict).problems[0].severity,severity)

    def test_unknown_events_require_event_context(self):
        agent = self.repo.root / 'devtool/lynx_devtool/agent/domain_agent/sample.cc'
        with agent.open('a') as out:
            out.write('''\n#include "DevToolSettings.h"\nLOGI("View.draw");\nmsg["method"] = "Sample.newEvent";\nSendEvent("Sample.anotherEvent");\nconst char* known = "Sample.changed";\nrequest["method"] = "Sample.get";\n''')
        contract, _ = model.load(self.repo)
        self.assertEqual(set(inventory.scan(self.repo, contract).events),
                         {'Sample.newEvent', 'Sample.anotherEvent', 'Sample.changed'})

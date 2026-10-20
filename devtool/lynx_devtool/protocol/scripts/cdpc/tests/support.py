# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.
import json
from pathlib import Path
import tempfile
import unittest
import yaml
from cdpc.model import Repo

class RepoTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Repo(Path(temporary.name))
        self.repo.upstream.parent.mkdir(parents=True)
        self.repo.upstream.write_text(json.dumps({'domains': [{'domain': 'Sample', 'types': [
            {'id':'Node','type':'object','properties':[{'name':'label','type':'string'},{'name':'next','$ref':'Node','optional':True}]},
            {'id':'Unused','type':'string'}], 'commands': [
            {'name':'get','parameters':[], 'returns':[{'name':'node','$ref':'Node'}]},
            {'name':'unused','parameters':[],'returns':[]}], 'events': [
            {'name':'changed','parameters':[{'name':'reason','type':'string','enum':['one']}]}]}]}))
        self.repo.support.write_text('{}\n')
        agent = self.repo.root / 'devtool/lynx_devtool/agent/domain_agent/sample.cc'
        agent.parent.mkdir(parents=True)
        agent.write_text('functions_map_["Sample.get"] = &Agent::Get;\n')
        (self.repo.root/'devtool/lynx_devtool/lynx_devtool_ng.cc').write_text('void LynxDevToolNG::RegisterInstanceDomainAgents() { RegisterAgent("Sample", agent); }')
        primjs = self.repo.root/'third_party/quickjs/src/src/inspector/protocols.cc'
        primjs.parent.mkdir(parents=True)
        primjs.write_text('const debug_function_type& GetDebugFunctionMap() { return {}; }')

    def doc(self, name='Sample.get', kind='method', **updates):
        domain, leaf = name.split('.')
        path = self.repo.docs/domain/('methods' if kind=='method' else 'events')/(leaf+'.yaml')
        path.parent.mkdir(parents=True, exist_ok=True)
        data={'summary':'Read a value.', 'parameters':[], 'examples':[], 'notes':[]}
        if kind=='method': data['returns']=[]
        data.update(updates)
        path.write_text(yaml.safe_dump(data, sort_keys=False))
        return path

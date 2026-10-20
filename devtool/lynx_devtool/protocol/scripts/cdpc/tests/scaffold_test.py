# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.
import yaml
from cdpc import model, scaffold
from cdpc.tests import cli_test
from cdpc.tests.support import RepoTest


class ScaffoldTest(RepoTest):
    run_cli = cli_test.CliTest.run_cli

    def test_new_loads_with_actionable_placeholders_and_never_overwrites(self):
        contract, _ = model.load(self.repo)
        path, problems = scaffold.create(self.repo, contract, 'Sample.newMethod')
        self.assertFalse(problems)
        original = path.read_bytes()
        self.assertEqual(set(yaml.safe_load(original)),
                         {'summary', 'parameters', 'returns', 'examples', 'notes'})
        loaded, problems = model.load(self.repo)
        self.assertIsNotNone(loaded.get('method', 'Sample.newMethod'))
        self.assertTrue(any(p.code == 'E101' and p.entry == 'Sample.newMethod' for p in problems))
        self.assertEqual(self.run_cli('new', 'Sample.newMethod')[0], 1)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.run_cli('check', 'Sample.newMethod')[0], 1)

    def test_upstream_fields_references_and_event_template(self):
        contract, _ = model.load(self.repo)
        path, problems = scaffold.create(self.repo, contract, 'Sample.get', from_upstream=True)
        self.assertFalse(problems)
        doc = yaml.safe_load(path.read_text())
        self.assertEqual(doc['returns'], [{'name': 'node', '$ref': 'Node'}])
        contract, problems = model.load(self.repo)
        self.assertFalse(any(p.code in ('E102', 'E103') for p in problems))
        self.assertEqual(self.run_cli('new', 'Sample.changed', '--event', '--from-upstream')[0], 0)
        event = yaml.safe_load((self.repo.docs / 'Sample/events/changed.yaml').read_text())
        self.assertNotIn('returns', event)
        self.assertEqual(event['parameters'][0]['enum'], ['one'])
        self.assertEqual(self.run_cli('new', 'Sample.noSuch', '--from-upstream')[0], 1)
        self.assertEqual(self.run_cli('new', '../outside')[0], 2)

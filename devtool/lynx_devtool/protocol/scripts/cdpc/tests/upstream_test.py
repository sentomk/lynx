# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.
import json
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch
import yaml

from cdpc import inventory, model, upstream
from cdpc.tests import cli_test
from cdpc.tests.support import RepoTest


class UpstreamTest(RepoTest):
    run_cli = cli_test.CliTest.run_cli

    def test_three_groups_and_pristine_field_comparison(self):
        self.repo.support.write_text(yaml.safe_dump({'Sample': {'deviations': [{
            'target': 'changed.parameters.reason', 'kind': 'retyped',
            'as': {'type': 'string'}, 'reason': 'Local reason strings.'}]}}))
        source = self.repo.root / 'devtool/lynx_devtool/agent/domain_agent/sample.cc'
        with source.open('a') as out:
            out.write('SendEvent("Sample.undeclared");')
        contract, _ = model.load(self.repo)
        sources = inventory.scan(self.repo, contract)
        contract = inventory.complete(contract, sources)
        report = upstream.diff(self.repo, contract, sources, 'Sample')
        self.assertEqual(report['unimplemented_upstream_methods'], ['Sample.unused'])
        self.assertEqual(report['undeclared_events'], ['Sample.undeclared'])
        field = report['deviations'][0]
        self.assertEqual(field['before']['schema']['enum'], ['one'])
        self.assertEqual(field['after']['schema'], {'type': 'string'})
        status, text = self.run_cli('upstream', 'diff', 'Sample', '--format', 'json')
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(text)['deviations'], report['deviations'])
        self.assertEqual(self.run_cli('upstream', 'diff', 'Missing')[0], 2)

    def test_update_runs_script_then_regenerates(self):
        with patch.object(upstream.subprocess, 'run') as run:
            run.return_value = subprocess.CompletedProcess([], 0, 'updated', '')
            status, text = self.run_cli('upstream', 'update', '--revision', 'pinned', '--format', 'json')
            self.assertEqual(status, 0)
            self.assertIn('--write', run.call_args.args[0])
            self.assertEqual(run.call_args.args[0][-2:], ['--revision', 'pinned'])
            self.assertTrue(self.repo.manifest.exists())
            self.assertEqual(json.loads(text)['update_log'], 'updated')
            run.return_value = subprocess.CompletedProcess([], 1, '', 'network failed')
            self.assertEqual(self.run_cli('upstream', 'update')[0], 2)

    def test_update_script_with_local_sources_is_atomic_on_invalid_input(self):
        source = Path(model.__file__).parents[1] / 'update_cdp_upstream.py'
        script = self.repo.protocol / 'scripts/update_cdp_upstream.py'
        script.parent.mkdir()
        shutil.copyfile(source, script)
        browser, js = self.repo.root / 'browser.json', self.repo.root / 'js.json'
        browser.write_text(json.dumps({'version': {'major': '1'}, 'domains': [{'domain': 'Browser'}]}))
        js.write_text(json.dumps({'version': {'major': '1'}, 'domains': [{'domain': 'Runtime'}]}))
        command = [sys.executable, str(script), '--write',
                   '--browser-protocol', str(browser), '--js-protocol', str(js)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        before = self.repo.upstream.read_bytes()
        self.assertEqual(len(json.loads(before)['domains']), 2)
        js.write_text(browser.read_text())  # Duplicate domain: must not replace the snapshot.
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 1)
        self.assertEqual(self.repo.upstream.read_bytes(), before)

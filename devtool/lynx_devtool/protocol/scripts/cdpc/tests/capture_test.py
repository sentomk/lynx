# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.
import json
import subprocess
from unittest.mock import patch

from cdpc import capture, model
from cdpc.tests import cli_test
from cdpc.tests.support import RepoTest


class CaptureTest(RepoTest):
    run_cli = cli_test.CliTest.run_cli
    def safe_doc(self, name='Sample.get', **updates):
        return self.doc(name, capture='safe', examples=[{'title': 'read',
            'request': {'id': 1, 'method': name}}], **updates)

    def test_default_unsafe_and_missing_dependency(self):
        self.doc(examples=[{'title': 'unsafe', 'request': {'id': 1, 'method': 'Sample.get'}}])
        with patch.object(capture.shutil, 'which', return_value=None):
            status, text = self.run_cli('capture', '--client', 'c', '--session', '1')
        self.assertEqual(status, 2)
        self.assertIn('npm install', text)
        with patch.object(capture.shutil, 'which', return_value='/agent'), patch.object(capture.subprocess, 'run') as run:
            self.assertEqual(self.run_cli('capture', '--client', 'c', '--session', '1')[0], 2)
            run.assert_not_called()

    def test_safe_replay_and_auto_verify_and_no_overwrite(self):
        self.safe_doc()
        path = self.repo.root / 'trace.jsonl'
        with patch.object(capture.shutil, 'which', return_value='/agent'), patch.object(capture.subprocess, 'run') as run:
            run.return_value = subprocess.CompletedProcess([], 0, '{"node":{"label":"ok"}}', '')
            args = ('capture', 'Sample', '--client', 'explicit', '--session', '1', '-o', str(path), '--format', 'json')
            status, output = self.run_cli(*args)
            self.assertEqual(status, 0, output)
            self.assertEqual(json.loads(output)['messages'], 2)
            command = run.call_args.args[0]
            self.assertEqual(command[:8], ['/agent', '--json', 'cdp', '--client', 'explicit', '--session', '1', '--method'])
            self.assertEqual(self.run_cli(*args)[0], 2)
            self.assertEqual(run.call_count, 1)

    def test_placeholder_prerequisite_and_filter(self):
        self.safe_doc('DOM.getDocument', returns=[{'name': 'root', 'type': 'object'}])
        self.doc('Sample.get', capture='safe', parameters=[{'name': 'nodeId', 'type': 'integer'}],
                 examples=[{'title': 'read', 'request': {'id': 1, 'method': 'Sample.get', 'params': {'nodeId': '$rootNodeId'}}}])
        contract, _ = model.load(self.repo)
        path = self.repo.root / 'trace.jsonl'
        responses = [subprocess.CompletedProcess([], 0, '{"root":{"nodeId":7}}', ''),
                     subprocess.CompletedProcess([], 0, '{"node":{"label":"ok"}}', '')]
        with patch.object(capture.shutil, 'which', return_value='/agent'), patch.object(capture.subprocess, 'run', side_effect=responses) as run:
            capture.capture(contract, ['Sample'], 'c', '1', path)
            self.assertEqual(json.loads(run.call_args_list[1].args[0][-1]), {'nodeId': 7})
        self.assertEqual(len(path.read_text().splitlines()), 4)

    def test_structured_errors_and_unstructured_failure(self):
        request = {'id': 4, 'method': 'Sample.get'}
        with patch.object(capture.subprocess, 'run') as run:
            run.return_value = subprocess.CompletedProcess([], 1, '{"error":{"code":-32602,"message":"bad"}}', '')
            self.assertEqual(capture.send('/agent', 'c', '1', request),
                             {'id': 4, 'error': {'code': -32602, 'message': 'bad'}})
            run.return_value = subprocess.CompletedProcess([], 1, '', 'Error: device disconnected')
            with self.assertRaisesRegex(OSError, 'without a structured JSON error'):
                capture.send('/agent', 'c', '1', request)
            run.side_effect = subprocess.TimeoutExpired(['agent'], 30)
            with self.assertRaisesRegex(OSError, 'timed out'):
                capture.send('/agent', 'c', '1', request)

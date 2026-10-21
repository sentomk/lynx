# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.
import io
import json
from pathlib import Path

from cdpc import inventory, model, trace
from cdpc.tests import cli_test
from cdpc.tests.support import RepoTest

DATA = Path(__file__).parent / 'testdata'


class TraceTest(RepoTest):
    def test_repository_compatibility_deviations(self):
        repo = model.Repo.current()
        contract, _ = model.load(repo)
        contract = inventory.complete(contract, inventory.scan(repo, contract))
        responses = {
            'DOM.getDocument': {'root': {'nodeId': 1, 'backendNodeId': 1, 'nodeType': 1,
                'nodeName': 'view', 'localName': 'view', 'nodeValue': ''}, 'compress': False},
            'CSS.getComputedStyleForNode': {'computedStyle': []},
            'Page.getResourceTree': {'frameTree': {'frame': {'url': 'lynx://',
                'securityOrigin': '', 'mimeType': 'text/html'}, 'resources': []}},
            'Lynx.getVersion': '4.1',
        }
        rows = []
        for id, (method, result) in enumerate(responses.items()):
            rows += [self.row('in', {'id': id, 'method': method, 'params': {'nodeId': 1}}),
                     self.row('out', {'id': id, 'result': result})]
        self.assertEqual(self.verify(rows, contract), ([], 8))
        rows[-1]['message']['result'] = 42
        problems, _ = self.verify(rows, contract)
        self.assertEqual(problems[0].problems, ['result: expected string'])

    def test_recorder_overflow_is_a_warning_even_with_target_filter(self):
        problems, count = self.verify([{'t': 1, 'dir': 'stats', 'channel': '0x0',
                                       'dropped_messages': 7}], targets=['Missing'])
        self.assertEqual(count, 0)
        self.assertEqual([(p.code, p.severity) for p in problems], [('W601', 'warning')])
        self.assertIn('7 messages', problems[0].message)

    def test_unanswered_uses_request_line(self):
        problems, _ = self.verify([
            self.row('in', {'id': 1, 'method': 'Sample.get'}),
            self.row('out', {'method': 'Sample.changed', 'params': {'reason': 'one'}}),
            self.row('out', {'method': 'Sample.changed', 'params': {'reason': 'one'}})])
        self.assertEqual([(p.code, p.line) for p in problems], [('kUnanswered', 1)])

    def contract(self):
        self.doc('Sample.changed', 'event',
                 parameters=[{'name': 'reason', 'type': 'string', 'enum': ['one']}])
        contract, _ = model.load(self.repo)
        return inventory.complete(contract, inventory.scan(self.repo, contract))

    def verify(self, rows, contract=None, targets=()):
        stream = io.StringIO('\n'.join(json.dumps(row) for row in rows))
        return trace.verify(contract or self.contract(), ['-'], targets, stream)

    def row(self, direction, message, channel='a', **extra):
        return {'t': 1, 'dir': direction, 'channel': channel, 'message': message, **extra}

    def test_all_nine_violations_and_fallback_suppression(self):
        problems, count = trace.verify(self.contract(), [DATA / 'violations.trace.jsonl'])
        self.assertEqual(count, 13)
        self.assertEqual([p.code for p in problems], [
            'kInvalidParams', 'kInvalidResult', 'kInvalidError', 'kInvalidEvent',
            'kUnknownMethod', 'kUnknownEvent', 'kDuplicateResponse', 'kDropped', 'kUnanswered'])
        self.assertEqual(problems[1].problems, [
            'result.node.label: missing required field', 'result.node.lable: unexpected field'])
        self.assertEqual(problems[1].line, 2)
        filtered, _ = trace.verify(self.contract(), [DATA / 'violations.trace.jsonl'], ['Sample.changed'])
        self.assertEqual([p.code for p in filtered], ['kInvalidEvent'])

    def test_channels_truncation_and_request_unknown_fields(self):
        rows = [
            self.row('in', {'id': 1, 'method': 'Sample.get', 'params': {'extra': 1}}),
            self.row('in', {'id': 1, 'method': 'Sample.get'}, 'b', truncated=True),
            self.row('out', {'id': 1, 'result': {'node': {'label': 'ok'}}}),
            self.row('out', {'id': 1}, 'b', truncated=True),
            self.row('out', {'method': 'Sample.changed'}, truncated=True),
        ]
        self.assertEqual(self.verify(rows), ([], 5))

    def test_dropped_without_fallback_and_success(self):
        rows = [self.row('in', {'id': 1, 'method': 'Sample.get'}),
                {'t': 2, 'dir': 'dropped', 'channel': 'a', 'id': 1}]
        problems, _ = self.verify(rows)
        self.assertEqual([p.code for p in problems], ['kDropped'])
        rows = [self.row('in', {'id': 1, 'method': 'Sample.get'}),
                self.row('out', {'id': 1, 'error': {'code': -32603, 'message': 'failed', 'data': {}}})]
        self.assertEqual(self.verify(rows), ([], 2))

    def test_invalid_input_and_unsupported_contract_are_explicit(self):
        for content in ('{', '{"t":NaN}', '{"t":1,"channel":"a","dir":"dropped"}'):
            with self.assertRaises(ValueError):
                trace.verify(self.contract(), ['-'], stdin=io.StringIO(content))
        self.doc('Sample.get', parameters=[{'name': 'value', 'type': 'string|missing'}])
        contract = self.contract()
        problems, _ = self.verify([
            self.row('in', {'id': 1, 'method': 'Sample.get'}),
            self.row('out', {'id': 1, 'result': {}})], contract)
        self.assertEqual([p.code for p in problems], ['kUnsupportedContract'])

    def test_cpp_recording_is_consumed_without_conversion(self):
        path = DATA / 'recorder.trace.jsonl'
        if not path.exists():
            self.skipTest('run devtool_base_unittest_exec --gtest_filter=*Recorder* first')
        repo = model.Repo.current()
        contract, _ = model.load(repo)
        contract = inventory.complete(contract, inventory.scan(repo, contract))
        problems, count = trace.verify(contract, [path])
        self.assertEqual(count, 5)
        self.assertEqual([(p.code, p.entry, p.id) for p in problems],
                         [('kDropped', 'Lynx.getLogLevel', 2)])


class VerifyCliTest(RepoTest):
    run_cli = cli_test.CliTest.run_cli

    def test_verify_uses_yaml_and_reports_json(self):
        self.doc('Sample.get', returns=[{'name': 'newField', 'type': 'string'}])
        path = self.repo.root / 'input.trace.jsonl'
        path.write_text(
            '{"t":1,"dir":"in","channel":"a","message":{"id":1,"method":"Sample.get"}}\n'
            '{"t":2,"dir":"out","channel":"a","message":{"id":1,"result":{"node":{"label":"ok"}}}}\n')
        status, text = self.run_cli('verify', str(path), '--format', 'json')
        self.assertEqual(status, 1)
        self.assertIn('result.newField: missing required field',
                      json.loads(text)['problems'][0]['problems'])
        self.assertFalse((self.repo.protocol / 'cdp_contract.generated.json').exists())
        self.assertEqual(self.run_cli('verify', str(path) + '.missing')[0], 2)

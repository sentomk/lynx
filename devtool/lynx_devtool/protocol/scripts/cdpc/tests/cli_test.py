# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
from cdpc import __main__ as cli
from cdpc.tests.support import RepoTest

class CliTest(RepoTest):
    def test_internal_keyerror_keeps_traceback(self):
        result = subprocess.run([sys.executable, '-c',
            'from unittest.mock import patch; from cdpc import __main__ as c; '
            'p=patch.object(c,"load",side_effect=KeyError("internal bug")); '
            'p.start(); raise SystemExit(c.main(["check"]))'], capture_output=True, text=True, cwd=Path(cli.__file__).resolve().parents[1])
        self.assertNotIn(result.returncode, (0, 2))
        self.assertIn('Traceback', result.stderr)
        self.assertIn("KeyError: 'internal bug'", result.stderr)

    def test_write_lists_unsupported_and_explicit_override(self):
        self.doc(parameters=[{'name': 'a', 'type': 'unsupported'}])
        status, text = self.run_cli('write')
        self.assertEqual(status, 1)
        self.assertIn('unsupported method omitted: Sample.get', text)
        data = json.loads((self.repo.protocol / 'cdp_contract.generated.json').read_text())
        self.assertEqual(data['unsupported'], {'methods': ['Sample.get'], 'events': []})
        self.assertEqual(self.run_cli('write', '--allow-unsupported')[0], 0)

    def run_cli(self,*args):
        out=io.StringIO()
        with patch.object(cli.model.Repo,'current',return_value=self.repo),contextlib.redirect_stdout(out):
            status=cli.main(list(args))
        return status,out.getvalue()

    def test_write_check_and_json_exit_status(self):
        self.assertEqual(self.run_cli('write')[0],0)
        status,text=self.run_cli('check','--strict','--format','json')
        self.assertEqual(status,0)
        self.assertEqual(json.loads(text)['problems'],[])
        self.doc(summary='TODO')
        status,text=self.run_cli('check','--format','json')
        self.assertEqual(status,1)
        self.assertTrue({'file','line','code','message'} <= json.loads(text)['problems'][0].keys())
        self.repo.upstream.unlink()
        self.assertEqual(self.run_cli('check')[0],2)

    def test_show_domains_entries_and_exact(self):
        self.doc(name='Sample.local')
        self.assertIn('domains',json.loads(self.run_cli('show','--format','json')[1]))
        rows=json.loads(self.run_cli('show','Sample','--format','json')[1])['entries']
        self.assertEqual(len(rows),2)
        exact=json.loads(self.run_cli('show','Sample.get','--format','json')[1])['entries'][0]
        self.assertIn('returns',exact)
        self.assertIsNotNone(exact['registration'])

    def test_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            cli.main(['migrate'])
        self.assertEqual(error.exception.code,2)

    def test_json_dry_run_is_one_json_document_and_writes_nothing(self):
        status, text = self.run_cli('write', '--dry-run', '--format', 'json')
        self.assertEqual(status, 0)
        self.assertEqual(len(json.loads(text)['files']), 3)
        self.assertFalse(self.repo.manifest.exists())

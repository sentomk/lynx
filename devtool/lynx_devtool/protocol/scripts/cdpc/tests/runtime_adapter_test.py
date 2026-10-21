# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.
import contextlib
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
import yaml

from cdpc import inventory, model
from cdpc.tests.support import RepoTest
from cdpc_core import config, descriptors
import cdpc_cpp
import cdpc_runtime
import cdpc_toolchain


class RuntimeAdapterTest(RepoTest):
    def contract(self):
        value,_=model.load(self.repo)
        return inventory.complete(value,inventory.scan(self.repo,value))

    def run_cli(self,*args):
        output=io.StringIO()
        with patch.object(model.Repo,'current',return_value=self.repo), contextlib.redirect_stdout(output):
            status=cdpc_runtime.main([*args,'--output-directory',str(self.repo.root/'generated'),'--format','json'])
        return status,json.loads(output.getvalue())

    def test_cpp_generation_keeps_references_shapes_and_identifiers(self):
        self.doc(parameters=[{'name':'choice','type':'string|null','enum':['ok']},
                             {'name':'map','type':'object','values':{'type':'integer'}},
                             {'name':'fixed','type':'object','properties':[]}])
        graph=descriptors.graph_data(self.contract())
        header,report=cdpc_cpp.generate(graph)
        self.assertEqual(cdpc_cpp.generate(graph),(header,report))
        self.assertEqual(report['methods'],['Sample.get'])
        self.assertFalse(report['omitted'])
        for expected in ('kSampleGet', 'Schema::Ref(', 'ObjectShape::Open',
                         'ObjectShape::Fixed', 'Type::String | Type::Null',
                         'inline constexpr MethodContract', 'std::array<MethodContract, 1>'):
            self.assertIn(expected,header)
        with self.assertRaisesRegex(ValueError,'version'):
            cdpc_cpp.generate({**graph,'version':2})

    def test_scalar_and_unsupported_roots_are_reported_and_never_wrapped(self):
        self.repo.support.write_text(yaml.safe_dump({'Sample':{'violations':[
            {'target':'get.returns','kind':'non-object-root','reason':'legacy'}]}}))
        self.doc(returns=[{'name':'result','type':'string','root':True}])
        header,report=cdpc_cpp.generate(descriptors.graph_data(self.contract()))
        self.assertEqual(report['methods'],[])
        self.assertEqual(report['omitted'][0]['name'],'Sample.get')
        self.assertNotIn('kSampleGet',header)
        self.assertIn('std::array<MethodContract, 0>',header)
        self.assertEqual(self.run_cli('write')[0],1)
        self.assertEqual(self.run_cli('check','--allow-unsupported')[0],0)
        self.doc(returns=[{'name':'field','type':'unknown'}])
        self.assertEqual(self.run_cli('write')[0],1)

    def test_symbol_collision_is_an_error_and_strings_preserve_all_bytes(self):
        self.doc('Sample.Get')
        with self.assertRaisesRegex(ValueError,'collision'):
            cdpc_cpp.generate(descriptors.graph_data(self.contract()))
        encoded=cdpc_cpp.literal('a\x001é"\\')
        self.assertIn('a\\0001\\303\\251\\"\\\\',encoded)
        self.assertTrue(encoded.endswith(', 7}'))

    def test_write_check_export_and_inventory_roundtrip(self):
        self.assertEqual(self.run_cli('check')[0],1)
        self.assertEqual(self.run_cli('write')[0],0)
        self.assertEqual(self.run_cli('check')[0],0)
        self.doc(parameters=[{'name':'text','type':'string'}])
        self.assertEqual(self.run_cli('check')[0],1)
        status,_=self.run_cli('export')
        self.assertEqual(status,0)
        settings=config.read(self.repo.root/'generated/cdpc.yaml')
        self.assertEqual(settings.inputs.docs,self.repo.docs.resolve())
        sources=config.read_inventory(settings.inventory)
        self.assertEqual(set(sources.methods),{'Sample.get'})
        self.assertEqual(self.run_cli('write','Missing')[0],2)

    def test_missing_dependency_is_json_environment_error(self):
        env={**os.environ,'CDPC_SOURCE_DIR':str(self.repo.root/'missing'),
             'PYTHONPATH':str(Path(cdpc_toolchain.__file__).parent)+os.pathsep+os.environ.get('PYTHONPATH','')}
        result=subprocess.run([sys.executable,'-m','cdpc_toolchain','check','--format','json'],
                              env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,2,result.stderr)
        self.assertEqual(json.loads(result.stdout)['problems'][0]['code'],'ENV')
        self.assertIn('CDPC_SOURCE_DIR',json.loads(result.stdout)['problems'][0]['message'])

    def test_pin_rejects_wrong_revision_and_dirty_tooling(self):
        cdpc_toolchain.activate.cache_clear()
        self.addCleanup(cdpc_toolchain.activate.cache_clear)
        with patch.object(cdpc_toolchain,'git',return_value='different'):
            with self.assertRaisesRegex(OSError,'revision differs'):
                cdpc_toolchain.activate()
        pin=json.loads(cdpc_toolchain.PIN.read_text())
        with patch.object(cdpc_toolchain,'git',side_effect=[pin['revision'],' M python/cdpc_core/model.py']):
            with self.assertRaisesRegex(OSError,'uncommitted'):
                cdpc_toolchain.activate()

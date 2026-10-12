"""Private module boundaries, using disposable filesystem fixtures."""
from contextlib import ExitStack
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import secretctl


class PrivateModuleTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name).resolve()
        self.ns = self.root / 'infrastructure/namespaces'
        self.ns.mkdir(parents=True)
        (self.ns / 'kustomization.yaml').write_text(
            'apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources: []\n')
        stack = self.enterContext(ExitStack())
        for name, value in [('ROOT', self.root), ('SECRET_DIR', self.root / 'secrets'),
                            ('NS_DIR', self.ns)]:
            stack.enter_context(patch.object(secretctl, name, value))

    def test_public_checks_work_without_private_access(self):
        self.assertEqual(secretctl.check_indexes(require_secrets=False), [])

    def test_secret_operations_reject_uninitialized_module(self):
        (self.root / 'secrets').mkdir()
        with self.assertRaisesRegex(ValueError, 'submodule'):
            secretctl.require_private_module()

    def test_public_checks_still_reject_broken_namespace_index(self):
        (self.ns / 'kustomization.yaml').write_text('resources: [missing.yaml]\n')
        with self.assertRaisesRegex(ValueError, 'Index differs'):
            secretctl.check_indexes(require_secrets=False)

    def test_private_source_credentials_cannot_be_user_managed(self):
        with self.assertRaisesRegex(ValueError, 'bootstrap'):
            secretctl.target('flux-system', 'infra-secrets')

    @unittest.skipUnless(shutil.which('sops') and shutil.which('age-keygen'), 'SOPS and age tools required')
    def test_encryption_matches_anchored_module_relative_rules(self):
        module = self.root / 'secrets'
        module.mkdir()
        identity = subprocess.check_output(['age-keygen'], stderr=subprocess.PIPE)
        recipient = subprocess.check_output(['age-keygen', '-y'], input=identity).decode().strip()
        config = module / '.sops.yaml'
        config.write_text(yaml.safe_dump({'creation_rules': [{
            'path_regex': r'^demo/.*\.sops\.yaml$', 'encrypted_regex': '^data$', 'age': recipient}]}))
        obj = {'apiVersion': 'v1', 'kind': 'Secret',
               'metadata': {'name': 'probe', 'namespace': 'demo'},
               'data': {'value': 'aGVsbG8='}}
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            with patch.object(secretctl, 'SOPS_CONFIG', config):
                raw = secretctl.encrypt(obj, module / 'demo/probe.sops.yaml')
            plain = secretctl.run(['sops', '--decrypt', '--input-type', 'yaml', '--output-type', 'yaml',
                                   '/dev/stdin'], data=raw, env=secretctl.key_env(identity))
            self.assertEqual(yaml.safe_load(plain)['data']['value'], 'aGVsbG8=')
        finally:
            os.chdir(previous)


if __name__ == '__main__':
    unittest.main()
